"""Read-only exact-content checks shared by dry and live publication."""

from dataclasses import dataclass
from hashlib import sha256
import re

from . import body, body_inspect, editor, subtitle
from ..errors import BrowserSessionError


@dataclass(frozen=True)
class PreparedDraftCheck:
    page: object
    surface: object
    title_field: object
    subtitle_field: object
    title: str
    expected_subtitle: str
    body_sha256: str
    evidence: tuple[str, ...]

    def require_unchanged(self):
        if (editor.title_value(self.title_field) != self.title
                or subtitle.subtitle_value(self.subtitle_field) != self.expected_subtitle
                or sha256(self.surface.inner_html().encode()).hexdigest() != self.body_sha256):
            raise BrowserSessionError('Prepared draft content changed before the final action.')
        if any(item.is_visible() for item in self.page.get_by_text(
                re.compile(r'^(Published|Sent)$', re.I)).all()):
            raise BrowserSessionError('Publication was already detected before the final action.')


def verify_prepared_content(page, story) -> PreparedDraftCheck:
    title_field = editor.unique_visible(editor.title_fields(page))
    subtitle_field, actual_subtitle = subtitle.inspect_subtitle(page)
    expected_subtitle = story.metadata.description or ''
    if (title_field is None or not title_field.is_editable()
            or editor.title_value(title_field) != story.metadata.title
            or subtitle_field is None or actual_subtitle != expected_subtitle):
        raise BrowserSessionError('The live title or subtitle does not match the exact source.')
    surface = body.locate_body_surface(page)
    before = surface.inner_html()
    prepared = body.prepare_story_body(story)
    report = body_inspect.inspect_body_formatting(surface, prepared)
    local, remote = report.prepared, report.remote
    if (before != surface.inner_html()
            or report.classification != body_inspect.FormattingClassification.VERIFIED
            or tuple(b.signature for b in local.blocks) != tuple(b.signature for b in remote.blocks)
            or any(a.status != 'match' for a in report.alignment)
            or body.normalize_visible_text(surface.inner_text()) != body.normalize_visible_text(prepared.text)):
        raise BrowserSessionError('The live body does not exactly match the prepared story blocks.')
    paragraphs = sum(b.kind == 'paragraph' for b in remote.meaningful)
    expected_paragraphs = sum(b.kind == 'paragraph' for b in local.meaningful)
    check = PreparedDraftCheck(
        page, surface, title_field, subtitle_field, story.metadata.title, expected_subtitle,
        sha256(before.encode()).hexdigest(), (
            'Exact title: Yes', 'Exact subtitle: Yes',
            f'Meaningful body blocks: {len(remote.meaningful)} / {len(local.meaningful)}',
            f'Headings matched: {report.headings_matched} / {report.headings_expected}',
            f'Story paragraphs matched: {paragraphs} / {expected_paragraphs}',
            f'Unintended blank paragraphs: {len(report.unexpected_blank_indexes)}',
            'Duplicated or missing story text: No', 'Exact story order: Yes',
        ),
    )
    check.require_unchanged()
    return check
