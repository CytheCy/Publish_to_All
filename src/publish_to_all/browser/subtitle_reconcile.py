"""Fresh rendered subtitle evidence, with no editor interactions or writes."""

from dataclasses import dataclass
from enum import StrEnum
import re

from playwright.sync_api import Error as PlaywrightError

from . import body, editor, subtitle
from .reconcile import verify_supplied_draft, verified_not_published_evidence
from ..errors import BrowserSessionError, SubstackRateLimitError


class SubtitleClassification(StrEnum):
    EXACT = 'SUBTITLE PRESENT AND EXACT'
    ABSENT = 'SUBTITLE ABSENT'
    UNKNOWN = 'SUBTITLE STATE UNKNOWN'


@dataclass(frozen=True)
class SubtitleEvidence:
    classification: SubtitleClassification
    value: str | None
    reason: str
    unpublished_verified: bool = False
    rate_limited: bool = False


def classify_subtitle(value: str | None, description: str) -> SubtitleClassification:
    # Match the insertion stage: exact equality for content, strip only to
    # determine whether the field is empty. Never normalize a nonempty value.
    if value is None or not description.strip():
        return SubtitleClassification.UNKNOWN
    if value == description:
        return SubtitleClassification.EXACT
    if not value.strip():
        return SubtitleClassification.ABSENT
    return SubtitleClassification.UNKNOWN


def _current_value(page, draft_url: str, title: str, monitor) -> str:
    monitor.require_clear(page)
    if page.url != draft_url or editor.saving_visible(page) or not editor.save_visible(page):
        raise BrowserSessionError('The exact draft editor is not stable.')
    field = editor.unique_visible(editor.title_fields(page))
    if field is None or not field.is_editable() or editor.title_value(field) != title:
        raise BrowserSessionError('The current draft title does not match exactly.')
    surface = body.locate_body_surface(page)
    if surface.inner_text().strip() or surface.locator(
        'img, video, audio, iframe, object, embed, hr'
    ).count():
        raise BrowserSessionError('The current draft body is not empty.')
    if any(item.is_visible() for item in page.get_by_text(
        re.compile(r'^(Published|Sent)$', re.I),
    ).all()):
        raise BrowserSessionError('The editor shows publication status.')
    # Resolve a fresh locator and read the live value property, never a cached
    # value attribute, a prior insertion result, or a Saved label as content.
    field, value = subtitle.inspect_subtitle(page)
    if field is None or value is None:
        raise BrowserSessionError('The current subtitle field is inaccessible or ambiguous.')
    monitor.require_clear(page)
    return value


def inspect_subtitle_for_reconciliation(
    page, publication_url: str, draft_url: str, title: str, description: str, monitor,
) -> SubtitleEvidence:
    """Open only the supplied draft and require two stable, live field reads."""
    unpublished = False
    try:
        evidence = verify_supplied_draft(
            page, publication_url, draft_url, rate_limit_monitor=monitor,
        )
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError('Substack rate limiting prevented inspection.')
        remote = evidence.inspection
        if (not evidence.verified or remote is None
                or remote.visible_title != title or remote.body_classification != 'empty'
                or verified_not_published_evidence(remote, draft_url) is None):
            raise BrowserSessionError('An authenticated, unpublished empty-body draft was not verified.')
        unpublished = True
        first = _current_value(page, draft_url, title, monitor)
        page.wait_for_timeout(500)
        second = _current_value(page, draft_url, title, monitor)
        if first != second:
            return SubtitleEvidence(
                SubtitleClassification.UNKNOWN, None, 'The subtitle changed during inspection.',
                unpublished,
            )
        classification = classify_subtitle(second, description)
        return SubtitleEvidence(
            classification, second,
            'Two fresh subtitle field reads agree.' if classification != SubtitleClassification.UNKNOWN
            else 'The nonempty subtitle differs from the expected Description.',
            unpublished,
        )
    except SubstackRateLimitError:
        return SubtitleEvidence(
            SubtitleClassification.UNKNOWN, None, 'Rate limiting; local state unchanged.',
            unpublished, True,
        )
    except (BrowserSessionError, PlaywrightError, AttributeError, TypeError):
        return SubtitleEvidence(
            SubtitleClassification.UNKNOWN, None,
            'The current draft or subtitle field could not be established safely.', unpublished,
            bool(monitor.encountered),
        )
