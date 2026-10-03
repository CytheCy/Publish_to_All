"""Read freshly loaded spacing evidence; never interact with the editor."""

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from playwright.sync_api import Error as PlaywrightError

from . import body, body_inspect, body_spacing as spacing, editor, reconcile, subtitle
from ..errors import BrowserSessionError, SubstackRateLimitError


class Classification(StrEnum):
    PERSISTED = 'SPACING REPAIR PERSISTED'
    NOT_PERSISTED = 'SPACING REPAIR DID NOT PERSIST'
    UNKNOWN = 'SPACING REPAIR STATE UNKNOWN'


@dataclass
class FreshRead:
    report: body_inspect.FormattingReport | None = None
    inspection: spacing.SpacingInspection | None = None
    safe_blanks: int | None = None
    title_preserved: bool | None = None
    subtitle_preserved: bool | None = None
    unpublished: bool = False
    rate_limited: bool = False
    reason: str = 'Fresh read unavailable.'

    def summary(self):
        report = self.report
        local = report.prepared.meaningful if report else ()
        remote = report.remote.meaningful if report else ()
        local_counts = Counter(b.signature for b in local)
        remote_counts = Counter(b.signature for b in remote)
        paragraphs = [b.signature for b in local if b.kind == 'paragraph']
        actual_paragraphs = [b.signature for b in remote if b.kind == 'paragraph']
        return {
            'prepared_meaningful_blocks': len(local) if report else None,
            'meaningful_blocks': len(remote) if report else None,
            'unwanted_blank_paragraphs': len(report.remote.blanks) if report else None,
            'safe_blank_candidates': self.safe_blanks,
            'headings_matched': report.headings_matched if report else None,
            'story_paragraphs_matched': sum(a == b for a, b in zip(paragraphs, actual_paragraphs))
                if report else None,
            'missing_blocks': sum((local_counts - remote_counts).values()) if report else None,
            'duplicate_blocks': sum(max(count - local_counts[sig], 0)
                                    for sig, count in remote_counts.items() if sig in local_counts)
                if report else None,
            'story_order_preserved': [b.signature for b in local] == [b.signature for b in remote]
                if report else None,
            'title_preserved': self.title_preserved,
            'subtitle_preserved': self.subtitle_preserved,
            'unpublished_verified': self.unpublished,
            'rate_limited': self.rate_limited,
            'exact_body_verified': self.inspection is not None,
            'body_sha256': self.inspection.fingerprint if self.inspection else None,
            'reason': self.reason,
        }


def _metadata(page, monitor, result):
    monitor.require_clear(page)
    editor.require_publication(page, spacing.PUBLICATION_URL)
    title = editor.unique_visible(editor.title_fields(page))
    _, actual_subtitle = subtitle.inspect_subtitle(page)
    result.title_preserved = bool(title is not None and title.is_editable()
                                  and editor.title_value(title) == spacing.TITLE)
    result.subtitle_preserved = actual_subtitle == spacing.SUBTITLE
    if page.url != spacing.DRAFT_URL or not result.title_preserved or not result.subtitle_preserved:
        raise BrowserSessionError('The exact draft URL, title or subtitle does not match.')


def read_fresh(page, prepared, monitor) -> FreshRead:
    """Navigate a new page once, then use the existing semantic/signature inspector."""
    result = FreshRead()
    try:
        evidence = reconcile.verify_supplied_draft(
            page, spacing.PUBLICATION_URL, spacing.DRAFT_URL, rate_limit_monitor=monitor,
        )
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError('Rate limiting prevented the fresh read.')
        remote = evidence.inspection
        if (not evidence.verified or remote is None
                or reconcile.verified_not_published_evidence(remote, spacing.DRAFT_URL) is None
                or remote.body_classification != 'substantial'):
            raise BrowserSessionError('An authenticated, editable, unpublished substantial draft was not verified.')
        result.unpublished = True
        _metadata(page, monitor, result)
        surface = body.locate_body_surface(page)
        before = surface.evaluate(spacing._SNAPSHOT)
        result.report = body_inspect.inspect_body_formatting(surface, prepared)
        after = surface.evaluate(spacing._SNAPSHOT)
        result.safe_blanks = sum(b['safe'] for b in after['blocks'])
        blank_count = len(result.report.remote.blanks)
        if blank_count not in (0, 36):
            raise BrowserSessionError('Fresh body has a partial or unexpected blank count.')
        inspection = spacing.validate_spacing_inspection(
            surface, prepared, result.report, before, after, repaired=blank_count == 0,
        )
        # Saved is only draft-state corroboration. The loaded content above is
        # the persistence evidence; no save observer or transition is requested.
        _metadata(page, monitor, result)
        result.inspection = inspection
        result.reason = 'Fresh server-loaded body and exact blank signature verified.'
    except SubstackRateLimitError:
        result.rate_limited = True
        result.reason = 'Rate limiting; no further navigation or local reconciliation.'
    except BrowserSessionError as exc:
        result.reason = str(exc)
    except PlaywrightError:
        result.reason = 'Fresh draft evidence could not be read confidently.'
    return result


def classify(reads) -> Classification:
    if len(reads) != 2 or any(
        r.inspection is None or r.rate_limited or not r.unpublished
        or r.title_preserved is not True or r.subtitle_preserved is not True for r in reads
    ):
        return Classification.UNKNOWN
    first, second = reads
    if (first.inspection.fingerprint != second.inspection.fingerprint
            or first.inspection.meaningful_exact != second.inspection.meaningful_exact):
        return Classification.UNKNOWN
    counts = tuple(len(r.inspection.candidates) for r in reads)
    if counts == (0, 0):
        return Classification.PERSISTED
    if counts == (36, 36):
        return Classification.NOT_PERSISTED
    return Classification.UNKNOWN
