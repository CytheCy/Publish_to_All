"""Read-only application operations reusable by any interface."""

from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config, runtime_paths
from .story import Story, load_story
from .state import PublicationRecord, PublicationRepository
from .browser.session import persistent_browser
from .browser.substack import AuthenticationState, collect_diagnostics, verify_page
from .errors import BrowserSessionError, SubstackRateLimitError
from .publishers.substack import SubstackPublisher


@dataclass(frozen=True)
class Inspection:
    config: Config
    story: Story


def inspect_project(root: Path) -> Inspection:
    config = load_config(root / "config.toml")
    return Inspection(config, load_story(root / "In"))


@dataclass(frozen=True)
class StoryStatus:
    story: Story
    substack: PublicationRecord | None


def inspect_status(root: Path) -> StoryStatus:
    """Read exact-version state, initializing only the schema if necessary."""
    story = load_story(root / "In")
    repository = PublicationRepository(runtime_paths(root).database)
    return StoryStatus(story, repository.get_publication(story.source_hash, "substack"))


def prepare_substack_draft(root: Path) -> StoryStatus:
    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    # Preserve first-run local state when a browser preflight cannot proceed.
    repository = PublicationRepository(paths.database) if paths.database.exists() else None
    if repository is not None:
        duplicate = repository.find_duplicate(story.source_hash, 'substack')
        if duplicate:
            repository.refuse_duplicate(story, duplicate)
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('No saved Substack session. Run publish-to-all substack-login first. No draft was created.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        authentication = verify_page(page, publication_url, 10, True)
        if authentication == AuthenticationState.RATE_LIMITED:
            diagnostic = collect_diagnostics(
                page, publication_url, paths.diagnostics, rate_limited=True,
            )
            raise SubstackRateLimitError(
                'Substack is temporarily rate limited.\n\n'
                'Substack is refusing requests from this browser session right now.\n\n'
                f'Final URL: {diagnostic.final_url}\n'
                f'Page title: {diagnostic.title}\n'
                'Rate limiting detected: Yes\n\n'
                'Retry manually later. Run publish-to-all substack-session first to check whether it has cleared.\n\n'
                'No local publication state was changed.\n'
                'No draft was created.\n'
                'Nothing was published.'
            )
        if authentication != AuthenticationState.AUTHENTICATED:
            raise BrowserSessionError(
                'Could not confirm an authenticated Substack session. Run publish-to-all substack-login. '
                'No draft was created. Nothing was published.'
            )
        # Repeat duplicate detection atomically after authentication and before mutation.
        repository = repository or PublicationRepository(paths.database)
        attempt = repository.begin_attempt(story, 'substack')
        publisher = SubstackPublisher(repository, page, publication_url, paths.diagnostics)
        record = publisher.prepare_draft(story, attempt)
    return StoryStatus(story, record)


def reconcile_substack(root: Path, *, link: bool = False, draft_url: str | None = None) -> str:
    """Read-only remotely; optionally link locally with corroborating attempt URL."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.reconcile import (
        inspect_drafts, DraftEvidence, SuppliedDraftEvidence, supplied_draft_diagnostics,
        validate_supplied_draft_url, verify_supplied_draft,
    )
    from .state import PublicationStatus

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    supplied_url = validate_supplied_draft_url(draft_url, publication_url) if draft_url is not None else None
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    attempt = repository.find_duplicate(inspection.story.source_hash, 'substack')
    if not attempt or attempt.status != PublicationStatus.FAILED or not attempt.needs_reconciliation:
        if supplied_url:
            return 'Unable to verify supplied draft URL safely.\n\nLocal state unchanged.\nNothing was published.'
        return 'No unresolved failed attempt for this exact story version. Local state unchanged.'
    if not paths.substack_browser_profile.is_dir():
        if supplied_url:
            return 'Unable to verify supplied draft URL safely.\n\nLocal state unchanged.\nNothing was published.'
        raise BrowserSessionError('No saved Substack session. Local state unchanged.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        if supplied_url:
            try:
                evidence = verify_supplied_draft(
                    page, publication_url, supplied_url, paths.diagnostics,
                )
            except (BrowserSessionError, PlaywrightError):
                evidence = SuppliedDraftEvidence(
                    False, 'Editor evidence could not be read safely.', diagnostics=supplied_draft_diagnostics(
                        page, publication_url, paths.diagnostics,
                    ),
                )
            if evidence.rate_limited:
                return _format_supplied_draft_failure(evidence, rate_limited=True)
            if not evidence.verified:
                return _format_supplied_draft_failure(evidence)
            record = repository.reconcile_failed_draft(
                attempt, supplied_url, verified_manual_url=True,
            )
            return '\n'.join([
                'Substack reconciliation complete', '', 'Story:', inspection.story.metadata.title, '',
                'Draft:', record.draft_url or supplied_url, '', 'Local state:', 'Draft created', '',
                'Duplicate protection remains active.', '', 'Nothing was published.',
            ])
        if verify_page(page, publication_url) != AuthenticationState.AUTHENTICATED:
            return 'Unknown: cannot confirm authentication. Local state unchanged; retry remains blocked.'
        try:
            evidence = inspect_drafts(page, publication_url, inspection.story.metadata.title)
        except (BrowserSessionError, PlaywrightError):
            evidence = DraftEvidence((), 'Cannot confidently inspect the rendered draft listing.')
    report = [evidence.reason, *evidence.matches]
    # Title alone cannot associate a pre-existing draft with this failed attempt.
    if len(evidence.matches) == 1 and attempt.draft_url and evidence.matches[0] == attempt.draft_url.rstrip('/'):
        if link:
            repository.reconcile_failed_draft(attempt, evidence.matches[0])
            report.append('Linked existing draft locally. No remote changes. Duplicate creation remains blocked.')
        else:
            report.append('Verified against the URL recorded by the failed attempt. Local state unchanged. '
                          'To link locally, run: publish-to-all substack-reconcile --link')
    else:
        report.append('Unknown: insufficient evidence to associate a unique draft with the failed attempt. '
                      'Local state unchanged; retry remains blocked. An untitled draft cannot be matched by story title.')
    return '\n'.join(report)


def _format_supplied_draft_failure(evidence, *, rate_limited: bool = False) -> str:
    report = ['RATE_LIMITED' if rate_limited else 'Unable to verify supplied draft URL safely.']
    if evidence.reason:
        report.extend(['', 'Reason: ' + evidence.reason])
    diagnostic = evidence.diagnostics
    if diagnostic is not None:
        report.extend([
            '', 'Final URL: ' + diagnostic.final_url,
            'Page title: ' + diagnostic.title,
            'Editable title field: ' + ('Yes' if diagnostic.title_field_present else 'No'),
            'Editable post editor: ' + ('Yes' if diagnostic.editor_surface_present else 'No'),
            'Visible editor controls: ' + (', '.join(diagnostic.controls) if diagnostic.controls else 'None recognized'),
            'Publish/Send control present: ' + ('Yes' if diagnostic.publish_or_send_present else 'No'),
            'Diagnostic screenshot: ' + (str(diagnostic.screenshot) if diagnostic.screenshot else 'Unavailable'),
        ])
    report.extend(['', 'Local state unchanged.', 'Nothing was published.'])
    return '\n'.join(report)
