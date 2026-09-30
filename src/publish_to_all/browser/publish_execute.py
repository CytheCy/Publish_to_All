"""Guarded, single-attempt execution of a positively identified final action."""

from dataclasses import dataclass, field
from enum import StrEnum
import re
from time import monotonic
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from .publish_inspect import (
    AttributeEvidence, FinalActionCandidate, FinalActionEvidence, FinalScreenInspection,
    inspect_final_action_evidence,
)
from .publish_validate import FinalPublishValidation, validate_final_publish_configuration
from .substack import trusted_page
from ..state import StateError


FINAL_ACTION_NAME = 'Send to everyone now'


@dataclass(frozen=True)
class FinalActionGuard:
    allowed: bool
    failures: tuple[str, ...]


def validate_final_action(evidence: FinalActionEvidence | None) -> FinalActionGuard:
    """Apply the complete execution guard to an immutable DOM snapshot."""
    if evidence is None:
        return FinalActionGuard(False, ('Final-action evidence is missing.',))
    failures: list[str] = []

    def require(condition: bool, reason: str) -> None:
        if not condition:
            failures.append(reason)

    require(evidence.exact_accessible_name == FINAL_ACTION_NAME,
            'The exact accessible name differs from "Send to everyone now".')
    require(evidence.total_matches == 1, 'Expected exactly one exact-name candidate.')
    require(evidence.visible_matches == 1, 'Expected exactly one visible exact-name candidate.')
    require(evidence.hidden_matches == 0, 'A hidden exact-name candidate exists.')
    require(evidence.enabled_visible_matches == 1,
            'Expected exactly one enabled visible exact-name candidate.')
    require(evidence.disabled_visible_matches == 0,
            'A disabled visible exact-name candidate exists.')
    require(evidence.publish_modal_matches == 1,
            'Expected exactly one div[role="dialog"][data-testid="publish-modal"].')
    require(not evidence.multiple_publish_modals, 'Multiple publish modals exist.')
    require(not evidence.ambiguity, 'The final-action evidence is ambiguous.')
    if len(evidence.candidates) != 1:
        return FinalActionGuard(False, tuple(failures))

    candidate = evidence.candidates[0]
    require(candidate.accessible_name == FINAL_ACTION_NAME,
            'The candidate accessible name is not exact.')
    require(candidate.tag_name == 'button', 'The candidate is not a native button.')
    require(candidate.role == 'button', 'The candidate role is not button.')
    require(candidate.element_type == AttributeEvidence(True, 'button'),
            'The candidate does not have explicit type="button".')
    require(candidate.effective_type == 'button', 'The candidate effective type is not button.')
    require(candidate.visible, 'The candidate is not visible.')
    require(candidate.enabled, 'The candidate is not enabled.')
    require(candidate.publish_modal_ancestor_count == 1,
            'The candidate does not have exactly one verified publish-modal ancestor.')
    require(candidate.belongs_to_exactly_one_publish_modal,
            'The candidate does not belong to exactly one verified publish modal.')
    require(not candidate.href.present, 'The candidate has an href attribute.')
    require(not candidate.target.present, 'The candidate has a target attribute.')
    require(not candidate.rel.present, 'The candidate has a rel attribute.')
    require(not candidate.form_attribute.present, 'The candidate has a form attribute.')
    require(candidate.form_owner is None, 'The candidate has a form owner.')
    require(candidate.nearest_form is None, 'The candidate has a nearest form ancestor.')
    require(not candidate.formaction.present, 'The candidate has a formaction attribute.')
    require(not candidate.formmethod.present, 'The candidate has a formmethod attribute.')
    return FinalActionGuard(not failures, tuple(failures))


@dataclass(frozen=True)
class FinalActionTarget:
    """Evidence plus the pinned DOM node from which that evidence was obtained."""

    evidence: FinalActionEvidence
    candidate: FinalActionCandidate
    element_handle: object = field(compare=False, repr=False)


def _guard_relevant_handle_evidence(handle) -> dict:
    return handle.evaluate(
        """node => {
            const attr = name => ({present: node.hasAttribute(name),
                value: node.hasAttribute(name) ? node.getAttribute(name) : ''});
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            const labelledBy = clean(node.getAttribute('aria-labelledby'));
            const labelledText = labelledBy ? labelledBy.split(/\\s+/).map(id => {
                const item = document.getElementById(id);
                return clean(item && (item.innerText || item.textContent));
            }).filter(Boolean).join(' ') : '';
            const accessibleName = clean(node.getAttribute('aria-label')) || labelledText ||
                clean(node.innerText) || clean(node.value) || clean(node.getAttribute('title'));
            const style = getComputedStyle(node), rect = node.getBoundingClientRect();
            const visible = !node.hidden && style.visibility !== 'hidden' &&
                style.display !== 'none' && style.opacity !== '0' &&
                (rect.width > 0 || rect.height > 0);
            const modals = [...document.querySelectorAll(
                'div[role="dialog"][data-testid="publish-modal"]')];
            const ancestors = modals.filter(modal => modal.contains(node));
            const type = attr('type');
            return {
                connected: node.isConnected, tagName: node.tagName.toLowerCase(),
                role: clean(node.getAttribute('role')) ||
                    (node.tagName === 'BUTTON' ? 'button' : ''),
                accessibleName, visible,
                enabled: !(node.matches(':disabled') ||
                    node.getAttribute('aria-disabled') === 'true'),
                type, effectiveType: node.tagName === 'BUTTON' ?
                    clean(type.present ? type.value : 'submit').toLowerCase() :
                    clean(type.value).toLowerCase(),
                href: attr('href'), target: attr('target'), rel: attr('rel'),
                formAttribute: attr('form'), formOwner: !!node.form,
                nearestForm: !!node.closest('form'), formaction: attr('formaction'),
                formmethod: attr('formmethod'), publishModalAncestorCount: ancestors.length,
                publishModalMatches: modals.length
            };
        }"""
    )


def collect_final_action_target(page) -> FinalActionTarget:
    """Collect evidence and pin its sole guarded node without interacting with it."""
    evidence = inspect_final_action_evidence(page)
    guard = validate_final_action(evidence)
    if not guard.allowed:
        raise ValueError('; '.join(guard.failures))
    locator = page.get_by_role('button', name=FINAL_ACTION_NAME, exact=True)
    if locator.count() != 1:
        raise ValueError('The semantic button locator no longer resolves to exactly one candidate.')
    handle = locator.element_handle()
    if handle is None:
        raise ValueError('The validated final-action candidate could not be pinned.')
    raw = _guard_relevant_handle_evidence(handle)
    candidate = evidence.candidates[0]
    expected = {
        'connected': True, 'tagName': candidate.tag_name, 'role': candidate.role,
        'accessibleName': candidate.accessible_name, 'visible': candidate.visible,
        'enabled': candidate.enabled,
        'type': {'present': candidate.element_type.present, 'value': candidate.element_type.value},
        'effectiveType': candidate.effective_type,
        'href': {'present': candidate.href.present, 'value': candidate.href.value},
        'target': {'present': candidate.target.present, 'value': candidate.target.value},
        'rel': {'present': candidate.rel.present, 'value': candidate.rel.value},
        'formAttribute': {
            'present': candidate.form_attribute.present, 'value': candidate.form_attribute.value,
        },
        'formOwner': candidate.form_owner is not None,
        'nearestForm': candidate.nearest_form is not None,
        'formaction': {'present': candidate.formaction.present, 'value': candidate.formaction.value},
        'formmethod': {'present': candidate.formmethod.present, 'value': candidate.formmethod.value},
        'publishModalAncestorCount': candidate.publish_modal_ancestor_count,
        'publishModalMatches': evidence.publish_modal_matches,
    }
    if raw != expected:
        raise ValueError('The pinned DOM candidate changed after final-action evidence collection.')
    return FinalActionTarget(evidence, candidate, handle)


class VerificationStatus(StrEnum):
    PUBLISHED = 'published'
    AMBIGUOUS = 'ambiguous'


@dataclass(frozen=True)
class PublicationVerification:
    status: VerificationStatus
    published_url: str | None
    evidence: tuple[str, ...]
    ambiguity_reason: str | None


def _public_post_url(value: str, publication_url: str) -> str | None:
    if not trusted_page(value, publication_url):
        return None
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return None
    if (parsed.hostname != urlsplit(publication_url).hostname or parsed.query or parsed.fragment
            or not re.fullmatch(r'/p/[^/]+', parsed.path.rstrip('/'))):
        return None
    return f'https://{parsed.hostname}{parsed.path.rstrip("/")}'


def verify_publication(page, publication_url: str, timeout: float = 10) -> PublicationVerification:
    """Require a specific public URL plus an explicit rendered publication status."""
    deadline = monotonic() + timeout
    last = None
    while True:
        try:
            raw = page.evaluate(
            """() => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = node => {
                    const style = getComputedStyle(node), rect = node.getBoundingClientRect();
                    return !node.hidden && style.visibility !== 'hidden' &&
                        style.display !== 'none' && style.opacity !== '0' &&
                        (rect.width > 0 || rect.height > 0);
                };
                const statuses = [...document.querySelectorAll('[role="status"], [aria-live], body *')]
                    .filter(visible).map(node => clean(node.innerText || node.textContent))
                    .filter(text => /^(Published|Sent|Post published|Publication successful)$/i.test(text));
                const canonicals = [...document.querySelectorAll('link[rel="canonical"][href]')]
                    .map(node => node.href);
                return {statuses: [...new Set(statuses)], canonicals,
                    publishModalCount: document.querySelectorAll(
                        'div[role="dialog"][data-testid="publish-modal"]').length};
            }"""
        )
            candidates = [page.url, *raw.get('canonicals', ())]
            public_urls = tuple(dict.fromkeys(
                url for value in candidates if (url := _public_post_url(value, publication_url))
            ))
            statuses = tuple(raw.get('statuses', ()))
            if len(public_urls) == 1 and statuses and raw.get('publishModalCount') == 0:
                return PublicationVerification(
                    VerificationStatus.PUBLISHED, public_urls[0],
                    (f'public_url={public_urls[0]}', f'explicit_status={statuses[0]}',
                     'publish_modal_absent'), None,
                )
            reason = (
                'Positive publication proof requires one trusted /p/<slug> URL, an explicit '
                'rendered Published/Sent confirmation, and an absent publish modal.'
            )
            last = PublicationVerification(
                VerificationStatus.AMBIGUOUS,
                public_urls[0] if len(public_urls) == 1 else None,
                tuple([*(f'candidate_public_url={url}' for url in public_urls),
                       *(f'rendered_status={status}' for status in statuses),
                       f'publish_modal_count={raw.get("publishModalCount", "unknown")}']), reason,
            )
        except (PlaywrightError, AttributeError, TypeError, ValueError):
            last = PublicationVerification(
                VerificationStatus.AMBIGUOUS, None, (),
                'Post-click publication evidence could not be read safely.',
            )
        if monotonic() >= deadline:
            return last
        page.wait_for_timeout(100)


class ExecutionStatus(StrEnum):
    BLOCKED = 'blocked'
    PUBLISHED = 'published'
    UNCERTAIN = 'uncertain'


@dataclass(frozen=True)
class PublishPreconditions:
    authenticated: bool
    duplicate_protection_passed: bool
    draft_identity_matches: bool


@dataclass(frozen=True)
class PublishExecutionResult:
    status: ExecutionStatus
    final_click_attempted: bool
    failures: tuple[str, ...]
    validation: FinalPublishValidation | None = None
    action_guard: FinalActionGuard | None = None
    verification: PublicationVerification | None = None
    publication: object | None = None


class GuardedFinalPublishExecutor:
    """Perform at most one click on one pinned, fully revalidated DOM node."""

    def __init__(self, repository, page, publication_url: str, record, draft_url: str,
                 inspect_screen, *, verifier=verify_publication):
        self.repository = repository
        self.page = page
        self.publication_url = publication_url.rstrip('/')
        self.record = record
        self.draft_url = draft_url.rstrip('/')
        self.inspect_screen = inspect_screen
        self.verifier = verifier
        self._final_click_attempted = False

    @property
    def final_click_attempted(self) -> bool:
        return self._final_click_attempted

    def _blocked(self, failures, validation=None, guard=None):
        return PublishExecutionResult(
            ExecutionStatus.BLOCKED, self._final_click_attempted, tuple(failures),
            validation, guard,
        )

    @staticmethod
    def _dom_snapshot(screen: FinalScreenInspection):
        return (
            screen.url, screen.title, screen.controls, screen.final_actions,
            screen.screen_kind, screen.screen_attributes, screen.scheduling,
            screen.final_action_evidence,
        )

    def execute(self, preconditions: PublishPreconditions,
                initial_screen: FinalScreenInspection) -> PublishExecutionResult:
        if self._final_click_attempted:
            return self._blocked(('This execution already attempted the final click.',))
        failures = []
        if not preconditions.authenticated:
            failures.append('Authenticated Substack preflight did not succeed.')
        if not preconditions.duplicate_protection_passed:
            failures.append('Story/draft duplicate protection did not succeed.')
        if not preconditions.draft_identity_matches:
            failures.append('The current draft identity does not match the publication attempt.')
        initial_validation = validate_final_publish_configuration(initial_screen)
        initial_guard = validate_final_action(initial_screen.final_action_evidence)
        if not initial_validation.ready_for_publish:
            failures.extend((*initial_validation.mismatches, *initial_validation.ambiguities))
        if not initial_guard.allowed:
            failures.extend(initial_guard.failures)
        if self.page.url.rstrip('/') != self.draft_url:
            failures.append('The draft URL changed before pre-click revalidation.')
        if failures:
            return self._blocked(failures, initial_validation, initial_guard)

        try:
            current_screen = self.inspect_screen()
        except (PlaywrightError, AttributeError, TypeError, ValueError):
            return self._blocked(('Final-screen pre-click inspection failed.',))
        current_validation = validate_final_publish_configuration(current_screen)
        current_guard = validate_final_action(current_screen.final_action_evidence)
        if self._dom_snapshot(current_screen) != self._dom_snapshot(initial_screen):
            failures.append('The final-screen inspection changed before the click.')
        if current_validation != initial_validation:
            failures.append('The publish configuration changed before the click.')
        if not current_validation.ready_for_publish:
            failures.extend((*current_validation.mismatches, *current_validation.ambiguities))
        if not current_guard.allowed:
            failures.extend(current_guard.failures)
        if self.page.url.rstrip('/') != self.draft_url:
            failures.append('The draft URL changed during pre-click revalidation.')
        if failures:
            return self._blocked(failures, current_validation, current_guard)

        try:
            target = collect_final_action_target(self.page)
        except (PlaywrightError, AttributeError, TypeError, ValueError) as exc:
            return self._blocked((f'Final-action candidate could not be pinned: {exc}',),
                                 current_validation, current_guard)
        if target.evidence != current_screen.final_action_evidence:
            return self._blocked(('Final-action evidence changed while pinning the candidate.',),
                                 current_validation, current_guard)
        if self.page.url.rstrip('/') != self.draft_url:
            return self._blocked(('The draft URL changed immediately before the click.',),
                                 current_validation, current_guard)

        # This durable compare-and-set is the final operation before Playwright is invoked.
        # If it fails, no browser mutation is attempted.
        try:
            active = self.repository.mark_final_click_attempted(self.record)
        except StateError as exc:
            return self._blocked((str(exc),), current_validation, current_guard)
        self.record = active
        self._final_click_attempted = True
        try:
            target.element_handle.click()
        except (Exception, KeyboardInterrupt):
            reason = ('The guarded final click raised an exception. A remote publication may have '
                      'occurred; no retry was attempted.')
            try:
                failed = self.repository.mark_publication_uncertain(active, reason, ())
            except StateError:
                failed = active
                reason += ' Local uncertainty persistence also failed; inspect SQLite before recovery.'
            return PublishExecutionResult(
                ExecutionStatus.UNCERTAIN, True, (reason,), current_validation,
                current_guard, None, failed,
            )

        try:
            verification = self.verifier(self.page, self.publication_url)
        except (Exception, KeyboardInterrupt):
            verification = PublicationVerification(
                VerificationStatus.AMBIGUOUS, None, (),
                'Post-click publication verification raised an exception.',
            )
        if verification.status is VerificationStatus.PUBLISHED and verification.published_url:
            try:
                published = self.repository.mark_publication_verified(
                    active, verification.published_url, verification.evidence,
                )
            except StateError:
                reason = (
                    'Publication was positively verified remotely, but local success persistence '
                    'failed. Inspect SQLite before recovery; no retry was attempted.'
                )
                return PublishExecutionResult(
                    ExecutionStatus.UNCERTAIN, True, (reason,), current_validation,
                    current_guard, verification, active,
                )
            return PublishExecutionResult(
                ExecutionStatus.PUBLISHED, True, (), current_validation, current_guard,
                verification, published,
            )
        reason = verification.ambiguity_reason or 'Publication outcome is ambiguous.'
        try:
            failed = self.repository.mark_publication_uncertain(active, reason, verification.evidence)
        except StateError:
            failed = active
            reason += ' Local uncertainty persistence also failed; inspect SQLite before recovery.'
        return PublishExecutionResult(
            ExecutionStatus.UNCERTAIN, True, (reason,), current_validation, current_guard,
            verification, failed,
        )
