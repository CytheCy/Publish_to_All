"""Conservative post-cover inspection and upload for an existing draft editor."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from time import monotonic

from playwright.sync_api import Error as PlaywrightError

from . import editor
from ..errors import BrowserSessionError


class CoverState(StrEnum):
    NONE = 'none'
    PRESENT = 'present'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class CoverControl:
    """One unambiguous semantic cover uploader and its interaction mode."""

    locator: object
    direct_input: bool


@dataclass(frozen=True)
class CoverDiagnostics:
    """Safe, fixed-vocabulary details explaining cover classification."""

    image_labels: tuple[str, ...] = ()
    cover_like_controls: tuple[str, ...] = ()
    cover_preview_present: bool = False
    cover_add_control_present: bool = False
    inline_image_controls_excluded: int = 0
    social_preview_controls_excluded: int = 0


@dataclass(frozen=True)
class CoverInspection:
    state: CoverState
    control: CoverControl | None = None
    diagnostics: CoverDiagnostics = CoverDiagnostics()


ADD_COVER = re.compile(
    r'^(?:add|upload|choose|select)(?: a| the)? '
    r'(?:(?:post|story|article) )?(?:cover|hero|header|featured) image$', re.I,
)
CHANGE_COVER = re.compile(
    r'^(?:change|replace|remove|delete|edit) (?:the )?'
    r'(?:(?:post|story|article) )?(?:cover|hero|header|featured) image$', re.I,
)
GENERIC_IMAGE = re.compile(r'^(?:add|upload|choose|select)(?: an?| the)? image$', re.I)
SEMANTIC_MARKER = re.compile(r'(?:cover|hero|featured|post[-_ ]?image)', re.I)
NON_COVER_MARKER = re.compile(r'(?:social|preview|email|banner|footer|thumbnail)', re.I)

CURRENT_FILE_SIDEBAR = '.post-editor-file-edit-sidebar.file-sidebar'
CURRENT_FILE_HEADER = re.compile(r'^File Settings$', re.I)
CURRENT_THUMBNAIL = re.compile(r'^Thumbnail$', re.I)
CURRENT_UPLOAD = re.compile(r'^(?:Add|Choose|Select|Upload)(?: an?)?(?: thumbnail| image)?$', re.I)
CURRENT_PRESENT = re.compile(
    r'^(?:Change|Replace|Remove|Delete|Edit)(?: the)?(?: thumbnail| image)?$', re.I,
)

COVER_REGIONS = (
    '[data-testid*="cover" i], [data-testid*="hero" i], '
    '[data-testid*="post-image" i], [data-testid*="featured-image" i], '
    '[aria-label*="cover image" i], [aria-label*="hero image" i], '
    '[class*="post-image" i], [class*="cover-image" i], [class*="hero-image" i]'
)


def _visible(items):
    return [item for item in items if item.is_visible()]


def _semantic_input(item) -> bool:
    attributes = ' '.join(
        item.get_attribute(name) or ''
        for name in ('id', 'name', 'aria-label', 'data-testid', 'class')
    )
    return bool(SEMANTIC_MARKER.search(attributes) and not NON_COVER_MARKER.search(attributes))


def _outside_non_cover_region(item) -> bool:
    try:
        return bool(item.evaluate(
            """node => !node.closest(
                '[data-testid*="social" i], [data-testid*="preview" i], '
                '[data-testid*="email" i], [data-testid*="banner" i], '
                '[class*="social-preview" i], [class*="email-header" i], '
                '[class*="banner" i], [aria-label*="social preview" i]'
            )"""
        ))
    except PlaywrightError:
        return False


def _outside_body_control(item) -> bool:
    """Allow a generic image label only in a semantic, non-body cover region."""
    try:
        return bool(item.evaluate(
            """node => !node.closest('[contenteditable="true"]') && !node.closest(
                '[data-testid*="social" i], [data-testid*="preview" i], '
                '[data-testid*="email" i], [data-testid*="banner" i], '
                '[class*="social-preview" i], [class*="email-header" i], '
                '[class*="banner" i], [aria-label*="social preview" i]'
            ) && !!node.closest(
                'header, [data-testid*="cover" i], [data-testid*="hero" i], '
                '[data-testid*="post-image" i], [data-testid*="featured-image" i], '
                '[class*="cover-image" i], [class*="hero-image" i], [class*="post-image" i]'
            )"""
        ))
    except PlaywrightError:
        return False


def _role_controls(page, pattern):
    return _visible(
        page.get_by_role('button', name=pattern)
        .or_(page.get_by_role('link', name=pattern))
        .or_(page.get_by_label(pattern)).all()
    )


def _visible_text(scope, pattern):
    return _visible(scope.get_by_text(pattern).all())


def _has_background_image(item) -> bool:
    try:
        return bool(item.evaluate(
            "node => getComputedStyle(node).backgroundImage !== 'none'"
        ))
    except PlaywrightError:
        return False


@dataclass(frozen=True)
class _CurrentThumbnailEvidence:
    controls: tuple[CoverControl, ...] = ()
    preview_count: int = 0
    present_control_count: int = 0
    upload_control_count: int = 0
    labels: tuple[str, ...] = ()
    recognized_items: int = 0


def _current_thumbnail_evidence(page) -> _CurrentThumbnailEvidence:
    """Read the current File Settings > Thumbnail UI without using body controls."""
    controls = []
    preview_count = present_count = upload_count = recognized_items = 0
    labels = []
    for sidebar in _visible(page.locator(CURRENT_FILE_SIDEBAR).all()):
        headers = _visible_text(sidebar, CURRENT_FILE_HEADER)
        if len(headers) != 1:
            continue
        for item in _visible(sidebar.locator('.file-sidebar-item').all()):
            thumbnail_labels = _visible_text(item, CURRENT_THUMBNAIL)
            if len(thumbnail_labels) != 1:
                continue
            recognized_items += 1
            labels.append('Thumbnail')
            images = _visible(item.locator('img, picture img').all())
            preview_nodes = _visible(item.locator(
                '[data-testid*="thumbnail" i], [class*="thumbnail-preview" i], '
                '[class*="image-preview" i]'
            ).all())
            preview_count += len(images)
            preview_count += sum(_has_background_image(node) for node in preview_nodes)
            present = _role_controls(item, CURRENT_PRESENT)
            present_count += len(present)
            if present:
                labels.append('Change/remove thumbnail')

            uploads = _visible_text(item, CURRENT_UPLOAD)
            upload_count += len(uploads)
            if uploads:
                labels.append('Upload')
            inputs = item.locator('input[type="file"][accept*="image" i]').all()
            if len(inputs) == 1:
                controls.append(CoverControl(inputs[0], True))
    return _CurrentThumbnailEvidence(
        tuple(controls), preview_count, present_count, upload_count,
        tuple(dict.fromkeys(labels)), recognized_items,
    )


def _excluded_control_count(page, selector: str) -> int:
    try:
        return len(_visible(page.locator(selector).all()))
    except PlaywrightError:
        return 0


def inspect_cover(page) -> CoverInspection:
    """Classify only an explicit post-cover region; body images are excluded."""
    try:
        current = _current_thumbnail_evidence(page)
        regions = [
            item for item in _visible(page.locator(COVER_REGIONS).all())
            if _outside_non_cover_region(item)
        ]
        cover_images = []
        for region in regions:
            cover_images.extend(_visible(region.locator('img, picture img').all()))
        change_controls = [
            item for item in _role_controls(page, CHANGE_COVER)
            if _outside_non_cover_region(item)
        ]
        present = bool(
            cover_images or change_controls
            or current.preview_count or current.present_control_count
        )

        # File inputs are commonly hidden behind a semantic button or label;
        # Playwright can safely set files on a uniquely identified hidden input.
        inputs = page.locator('input[type="file"][accept*="image" i]').all()
        semantic_inputs = [
            item for item in inputs
            if _semantic_input(item) and _outside_non_cover_region(item)
        ]
        explicit = [
            item for item in _role_controls(page, ADD_COVER)
            if _outside_non_cover_region(item)
        ]
        generic = [item for item in _role_controls(page, GENERIC_IMAGE) if _outside_body_control(item)]

        candidates = [CoverControl(item, True) for item in semantic_inputs]
        candidates += [CoverControl(item, False) for item in explicit + generic]
        current_none = bool(
            current.recognized_items == 1
            and len(current.controls) == 1
            and current.upload_control_count == 1
            and not current.preview_count
            and not current.present_control_count
        )
        if current_none:
            candidates += current.controls

        inline_excluded = _excluded_control_count(
            page,
            '[contenteditable="true"] button[title*="image" i], '
            '.editor button[title*="image" i], button[title="Insert image" i]',
        )
        social_excluded = _excluded_control_count(
            page,
            '[data-testid*="social" i] button, [data-testid*="social" i] input[type="file"], '
            '[class*="social-preview" i] button, [class*="social-preview" i] input[type="file"], '
            '[aria-label*="social preview" i] button, '
            '[aria-label*="social preview" i] input[type="file"]',
        )
        labels = list(current.labels)
        if explicit:
            labels.append('Add cover image')
        diagnostics = CoverDiagnostics(
            tuple(dict.fromkeys(labels)),
            tuple(filter(None, (
                f'{current.recognized_items} File Settings thumbnail item(s)'
                if current.recognized_items else '',
                f'{len(semantic_inputs)} semantic cover file input(s)'
                if semantic_inputs else '',
                f'{len(explicit)} explicit cover add control(s)' if explicit else '',
                f'{len(change_controls) + current.present_control_count} cover change/remove control(s)'
                if change_controls or current.present_control_count else '',
            ))),
            bool(cover_images or current.preview_count),
            bool(current_none or semantic_inputs or explicit or generic),
            inline_excluded,
            social_excluded,
        )
        if present and (semantic_inputs or explicit or generic) and not (
                current.preview_count or current.present_control_count):
            return CoverInspection(CoverState.UNKNOWN, diagnostics=diagnostics)
        if present:
            return CoverInspection(CoverState.PRESENT, diagnostics=diagnostics)
        # The same uploader can be exposed as both a labeled input and a button.
        # Prefer the direct semantic input only when it is uniquely identifiable.
        if current_none and not semantic_inputs and not explicit and not generic:
            return CoverInspection(CoverState.NONE, current.controls[0], diagnostics)
        if len(semantic_inputs) == 1:
            return CoverInspection(
                CoverState.NONE, CoverControl(semantic_inputs[0], True), diagnostics,
            )
        if not semantic_inputs and len(explicit + generic) == 1:
            return CoverInspection(CoverState.NONE, candidates[0], diagnostics)
        return CoverInspection(CoverState.UNKNOWN, diagnostics=diagnostics)
    except PlaywrightError:
        return CoverInspection(CoverState.UNKNOWN)


def upload_cover(page, control: CoverControl, image: Path) -> None:
    """Select the original file through one semantic UI file uploader."""
    if control.direct_input:
        control.locator.set_input_files(str(image))
        return
    with page.expect_file_chooser(timeout=5000) as chooser:
        control.locator.click()
    chooser.value.set_files(str(image))


def _saving_visible(page) -> bool:
    return any(item.is_visible() for item in page.get_by_text(editor.SAVING).all())


def _require_same_content(page, title_field, expected_title: str, body_surface, body_text: str,
                          publication_url: str, draft_url: str) -> None:
    editor.require_publication(page, publication_url)
    if editor.extract_draft_url(page, publication_url) != draft_url:
        raise BrowserSessionError('The editor left the linked draft during image upload.')
    if editor.title_value(title_field).strip() != expected_title:
        raise BrowserSessionError('The draft title changed during image upload.')
    if ' '.join(body_surface.inner_text().split()) != body_text:
        raise BrowserSessionError('The draft body changed during image upload.')


def confirm_cover_and_save(
    page, title_field, expected_title: str, body_surface, body_text: str,
    publication_url: str, draft_url: str, monitor, *, previously_saved: bool,
    timeout: float = 30,
) -> None:
    """Require cover appearance, preserved content, and a fresh autosave signal."""
    deadline = monotonic() + timeout
    saw_unsaved = not previously_saved
    saw_cover = False
    while True:
        monitor.require_clear(page)
        _require_same_content(
            page, title_field, expected_title, body_surface, body_text,
            publication_url, draft_url,
        )
        inspection = inspect_cover(page)
        saw_cover = saw_cover or inspection.state == CoverState.PRESENT
        saved = editor.save_visible(page)
        saving = _saving_visible(page)
        saw_unsaved = saw_unsaved or not saved or saving
        if saw_cover and saved and not saving and saw_unsaved:
            monitor.require_clear(page)
            return
        if monotonic() >= deadline:
            if not saw_cover:
                raise BrowserSessionError('The uploaded cover image did not appear in the editor.')
            raise BrowserSessionError('A fresh Saved signal did not confirm the cover image upload.')
        page.wait_for_timeout(200)
