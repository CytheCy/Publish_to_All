"""Conservative post-cover inspection and upload for an existing draft editor."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import re
from time import monotonic

from PIL import Image, UnidentifiedImageError
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
    scope: object | None = None
    upload_control: object | None = None


@dataclass(frozen=True)
class UploadImageInfo:
    """Locally verified image properties safe to include in diagnostics."""

    size_bytes: int
    width: int
    height: int
    mime_type: str


@dataclass(frozen=True)
class CoverDiagnostics:
    """Safe, fixed-vocabulary details explaining cover classification."""

    image_labels: tuple[str, ...] = ()
    cover_like_controls: tuple[str, ...] = ()
    cover_preview_present: bool = False
    cover_add_control_present: bool = False
    inline_image_controls_excluded: int = 0
    social_preview_controls_excluded: int = 0
    candidate_image_inputs: int = 0
    thumbnail_image_inputs: int = 0
    input_accept: str = ''
    input_attached: bool | None = None
    input_enabled: bool | None = None
    input_visible: bool | None = None
    input_labels: tuple[str, ...] = ()
    upload_progress_present: bool = False
    input_replaced: bool = False


@dataclass
class UploadTrace:
    """Safe fixed-vocabulary evidence from one file-selection attempt."""

    completed: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    current: str = 'Open File Settings'

    def start(self, checkpoint: str) -> None:
        self.current = checkpoint

    def complete(self, checkpoint: str, observation: str | None = None) -> None:
        self.current = checkpoint
        if checkpoint not in self.completed:
            self.completed.append(checkpoint)
        if observation and observation not in self.observations:
            self.observations.append(observation)

    def text(self) -> str:
        completed = ', '.join(self.completed) or 'None'
        observations = '; '.join(self.observations) or 'None'
        return f'Completed checkpoints: {completed}\nSafe observations: {observations}'


@dataclass(frozen=True)
class UploadEvents:
    """Safe event and mutation counts; filenames and file contents are never read."""

    input_events: int = 0
    change_events: int = 0
    input_replaced: bool = False
    file_reader_started: bool = False
    processing_appeared: bool = False
    preview_mutated: bool = False


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
FILE_SETTINGS_CONTROL = re.compile(r'^File Settings$', re.I)
UPLOAD_PROGRESS_TEXT = re.compile(r'^(?:Uploading|Processing)(?: image| thumbnail)?(?:\.{3}|…)?$', re.I)
UPLOAD_PROGRESS_SELECTOR = (
    'progress, [role="progressbar"], [aria-busy="true"], '
    '[class*="upload-progress" i], [class*="uploading" i], '
    '[class*="processing" i], [data-testid*="upload-progress" i]'
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


def _input_state(item) -> tuple[bool, bool, bool, int]:
    """Return only safe browser-side state; never return a filename or file contents."""
    try:
        state = item.evaluate(
            "node => ({attached: node.isConnected, enabled: !node.disabled, "
            "visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length), "
            "files: node.files ? node.files.length : 0})"
        )
        return (
            bool(state.get('attached')), bool(state.get('enabled')),
            bool(state.get('visible')), int(state.get('files') or 0),
        )
    except (PlaywrightError, AttributeError, TypeError, ValueError):
        return False, False, False, 0


def _accepts_image(item) -> bool:
    try:
        accept = (item.get_attribute('accept') or '').lower()
        return 'image/' in accept or any(
            extension in accept for extension in ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        )
    except PlaywrightError:
        return False


def _safe_accept(item) -> str:
    try:
        accept = (item.get_attribute('accept') or '').strip()
        return accept[:200] if accept else 'not specified'
    except PlaywrightError:
        return 'unavailable'


def validate_upload_image(path: Path) -> UploadImageInfo:
    """Read and verify upload metadata without changing or recompressing the source."""
    try:
        size = path.stat().st_size
        with Image.open(path) as source:
            source.verify()
        with Image.open(path) as source:
            source.load()
            image_format = source.format
            width, height = source.size
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError) as exc:
        raise BrowserSessionError('The image source is not a valid readable image.') from exc
    mime_type = Image.MIME.get(image_format or '', '')
    if not size or width < 1 or height < 1 or not mime_type.startswith('image/'):
        raise BrowserSessionError('The image source has invalid upload properties.')
    return UploadImageInfo(size, width, height, mime_type)


def _mark_input_node(item) -> None:
    item.evaluate('node => { window.__publishToAllThumbnailInput = node; }')


def _same_input_node(item) -> bool:
    try:
        return bool(item.evaluate('node => node === window.__publishToAllThumbnailInput'))
    except PlaywrightError:
        return False


def _install_upload_diagnostics(item) -> None:
    """Install narrowly scoped counters before selection; never inspect file names or bytes."""
    item.evaluate(
        """node => {
            const scope = node.closest('.file-sidebar-item') || node.parentElement;
            const state = {
                original: node, scope, active: false, inputEvents: 0, changeEvents: 0,
                inputReplaced: false, fileReaderStarted: false,
                processingAppeared: false, previewMutated: false
            };
            window.__publishToAllUploadDiagnostics = state;
            const relevant = target => target instanceof HTMLInputElement &&
                target.type === 'file' && !!scope && scope.contains(target);
            document.addEventListener('input', event => {
                if (state.active && relevant(event.target)) state.inputEvents += 1;
            }, true);
            document.addEventListener('change', event => {
                if (state.active && relevant(event.target)) state.changeEvents += 1;
            }, true);
            const progressSelector = 'progress, [role="progressbar"], [aria-busy="true"], ' +
                '[class*="upload-progress" i], [class*="uploading" i], ' +
                '[class*="processing" i], [data-testid*="upload-progress" i]';
            const previewSelector = 'img, picture, [class*="thumbnail-preview" i], ' +
                '[class*="image-preview" i], [data-testid*="thumbnail" i]';
            state.observer = new MutationObserver(records => {
                if (!state.active || !scope) return;
                const inputs = scope.querySelectorAll('input[type="file"]');
                state.inputReplaced ||= !state.original.isConnected ||
                    (inputs.length === 1 && inputs[0] !== state.original);
                state.processingAppeared ||= !!scope.querySelector(progressSelector);
                for (const record of records) {
                    const target = record.target.nodeType === 1 ?
                        record.target : record.target.parentElement;
                    if (target && scope.contains(target) &&
                            (target.matches?.(previewSelector) || target.closest?.(previewSelector) ||
                             target.querySelector?.(previewSelector))) {
                        state.previewMutated = true;
                    }
                }
            });
            if (scope) state.observer.observe(scope, {
                subtree: true, childList: true, attributes: true
            });
            if (!window.__publishToAllFileReaderWrapped && window.FileReader) {
                window.__publishToAllFileReaderWrapped = true;
                for (const method of ['readAsArrayBuffer', 'readAsBinaryString',
                                      'readAsDataURL', 'readAsText']) {
                    const original = FileReader.prototype[method];
                    if (typeof original !== 'function') continue;
                    FileReader.prototype[method] = function(...args) {
                        const current = window.__publishToAllUploadDiagnostics;
                        if (current?.active) current.fileReaderStarted = true;
                        return original.apply(this, args);
                    };
                }
            }
        }"""
    )


def _activate_upload_diagnostics(item) -> None:
    item.evaluate(
        "node => { const state = window.__publishToAllUploadDiagnostics; "
        "if (state && state.original === node) state.active = true; }"
    )


def _upload_events(item) -> UploadEvents:
    try:
        state = item.evaluate(
            """node => {
                const value = window.__publishToAllUploadDiagnostics;
                if (!value) return {};
                const inputs = value.scope?.querySelectorAll('input[type="file"]') || [];
                return {
                    inputEvents: value.inputEvents, changeEvents: value.changeEvents,
                    inputReplaced: value.inputReplaced || !value.original.isConnected ||
                        (inputs.length === 1 && inputs[0] !== value.original),
                    fileReaderStarted: value.fileReaderStarted,
                    processingAppeared: value.processingAppeared,
                    previewMutated: value.previewMutated
                };
            }"""
        )
        return UploadEvents(
            int(state.get('inputEvents') or 0), int(state.get('changeEvents') or 0),
            bool(state.get('inputReplaced')), bool(state.get('fileReaderStarted')),
            bool(state.get('processingAppeared')), bool(state.get('previewMutated')),
        )
    except (PlaywrightError, AttributeError, TypeError, ValueError):
        return UploadEvents()


def _upload_event_note(events: UploadEvents) -> str:
    return (
        f'input event: {"Yes" if events.input_events else "No"}; '
        f'change event: {"Yes" if events.change_events else "No"}; '
        f'input replaced: {"Yes" if events.input_replaced else "No"}; '
        f'FileReader started: {"Yes" if events.file_reader_started else "No"}; '
        f'processing appeared: {"Yes" if events.processing_appeared else "No"}; '
        f'preview DOM mutated: {"Yes" if events.preview_mutated else "No"}'
    )


def _upload_progress(scope) -> bool:
    try:
        nodes = _visible(scope.locator(UPLOAD_PROGRESS_SELECTOR).all())
        texts = _visible_text(scope, UPLOAD_PROGRESS_TEXT)
        return bool(nodes or texts)
    except PlaywrightError:
        return False


def _thumbnail_preview_count(scope) -> int:
    try:
        images = [node for node in _visible(scope.locator('img, picture img').all())
                  if node.get_attribute('src')]
        preview_nodes = _visible(scope.locator(
            '[data-testid*="thumbnail" i], [class*="thumbnail-preview" i], '
            '[class*="image-preview" i]'
        ).all())
        return len(images) + sum(_has_background_image(node) for node in preview_nodes)
    except PlaywrightError:
        return 0


def _replacement_input_present(scope, original) -> bool:
    if scope is None:
        return False
    try:
        for candidate in scope.locator('input[type="file"]').all():
            if not _accepts_image(candidate):
                continue
            attached, enabled, _, _ = _input_state(candidate)
            if attached and enabled:
                return True
    except PlaywrightError:
        pass
    return False


@dataclass(frozen=True)
class _CurrentThumbnailEvidence:
    controls: tuple[CoverControl, ...] = ()
    preview_count: int = 0
    present_control_count: int = 0
    upload_control_count: int = 0
    labels: tuple[str, ...] = ()
    recognized_items: int = 0
    candidate_inputs: int = 0
    thumbnail_inputs: int = 0
    input_accept: str = ''
    input_attached: bool | None = None
    input_enabled: bool | None = None
    input_visible: bool | None = None
    progress_present: bool = False
    scope: object | None = None


def _file_settings_sidebars(page):
    sidebars = []
    for sidebar in _visible(page.locator(CURRENT_FILE_SIDEBAR).all()):
        if len(_visible_text(sidebar, CURRENT_FILE_HEADER)) == 1:
            sidebars.append(sidebar)
    return sidebars


def open_file_settings(page, *, timeout: float = 5):
    """Return the one current File Settings panel, opening its exact control if needed."""
    sidebars = _file_settings_sidebars(page)
    if len(sidebars) == 1:
        return sidebars[0]
    if len(sidebars) > 1:
        raise BrowserSessionError('Multiple visible File Settings panels were found.')
    controls = _role_controls(page, FILE_SETTINGS_CONTROL)
    if len(controls) != 1:
        raise BrowserSessionError('The exact File Settings control could not be identified.')
    controls[0].click()
    deadline = monotonic() + timeout
    while True:
        sidebars = _file_settings_sidebars(page)
        if len(sidebars) == 1:
            return sidebars[0]
        if len(sidebars) > 1 or monotonic() >= deadline:
            raise BrowserSessionError('The File Settings panel did not open unambiguously.')
        page.wait_for_timeout(100)


def _current_thumbnail_evidence(page, sidebar=None) -> _CurrentThumbnailEvidence:
    """Read the current File Settings > Thumbnail UI without using body controls."""
    controls = []
    preview_count = present_count = upload_count = recognized_items = 0
    labels = []
    candidate_inputs = sum(
        _accepts_image(candidate)
        for candidate in page.locator('input[type="file"]').all()
    )
    thumbnail_inputs = 0
    selected_state = (None, None, None)
    selected_accept = ''
    progress_present = False
    sidebars = [sidebar] if sidebar is not None else _file_settings_sidebars(page)
    for current_sidebar in sidebars:
        for item in _visible(current_sidebar.locator('.file-sidebar-item').all()):
            thumbnail_labels = _visible_text(item, CURRENT_THUMBNAIL)
            if len(thumbnail_labels) != 1:
                continue
            recognized_items += 1
            labels.append('Thumbnail')
            preview_count += _thumbnail_preview_count(item)
            present = _role_controls(item, CURRENT_PRESENT)
            present_count += len(present)
            if present:
                labels.append('Change/remove thumbnail')

            uploads = _visible_text(item, CURRENT_UPLOAD)
            upload_count += len(uploads)
            if uploads:
                labels.append('Upload')
            progress_present = progress_present or _upload_progress(item)
            inputs = [candidate for candidate in item.locator('input[type="file"]').all()
                      if _accepts_image(candidate)]
            thumbnail_inputs += len(inputs)
            valid_inputs = []
            for candidate in inputs:
                attached, enabled, visible, _ = _input_state(candidate)
                if attached and enabled:
                    valid_inputs.append(candidate)
                    selected_state = attached, enabled, visible
                    selected_accept = _safe_accept(candidate)
            if len(inputs) == 1 and len(valid_inputs) == 1 and len(uploads) == 1:
                controls.append(CoverControl(valid_inputs[0], True, item, uploads[0]))
    return _CurrentThumbnailEvidence(
        tuple(controls), preview_count, present_count, upload_count,
        tuple(dict.fromkeys(labels)), recognized_items, candidate_inputs,
        thumbnail_inputs, selected_accept, *selected_state, progress_present,
        sidebars[0] if len(sidebars) == 1 else None,
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
            if (_semantic_input(item) and _outside_non_cover_region(item)
                and _input_state(item)[0] and _input_state(item)[1])
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
            current.candidate_inputs,
            current.thumbnail_inputs,
            current.input_accept,
            current.input_attached,
            current.input_enabled,
            current.input_visible,
            current.labels,
            current.progress_present,
        )
        if present and (semantic_inputs or explicit or generic) and not (
                current.preview_count or current.present_control_count):
            return CoverInspection(CoverState.UNKNOWN, diagnostics=diagnostics)
        if present:
            return CoverInspection(CoverState.PRESENT, diagnostics=diagnostics)
        # The same uploader can be exposed as both a labeled input and a button.
        # Prefer the direct semantic input only when it is uniquely identifiable.
        if current_none:
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


def upload_cover(page, control: CoverControl, image: Path, *, trace: UploadTrace | None = None) -> None:
    """Select one file once through a chooser and observe the uploader's reaction."""
    trace = trace or UploadTrace()
    trace.start('Confirm file input attached/enabled')
    if control.direct_input:
        attached, enabled, visible, _ = _input_state(control.locator)
        trace.complete(
            'Confirm file input attached/enabled',
            f'Input attached: {"Yes" if attached else "No"}; enabled: '
            f'{"Yes" if enabled else "No"}; visible: {"Yes" if visible else "No"}',
        )
        if not attached or not enabled:
            raise BrowserSessionError('The thumbnail image input is detached or disabled.')
        _mark_input_node(control.locator)
        _install_upload_diagnostics(control.locator)
        _activate_upload_diagnostics(control.locator)
        trace.start('Set file')
        chooser = None
        if control.upload_control is not None:
            try:
                with page.expect_file_chooser(timeout=2000) as pending:
                    control.upload_control.click()
                chooser = pending.value
                trace.observations.append(
                    'Visible Thumbnail Upload control opened the file chooser'
                )
            except PlaywrightError:
                trace.observations.append(
                    'Visible Thumbnail Upload control did not open a chooser; '
                    'used its uniquely associated input chooser'
                )
        if chooser is None:
            with page.expect_file_chooser(timeout=5000) as pending:
                control.locator.evaluate('node => node.click()')
            chooser = pending.value
        chooser.set_files(str(image))
        trace.complete('Set file', 'File chooser accepted one selection')
    else:
        trace.complete('Confirm file input attached/enabled', 'File chooser control ready')
        trace.start('Set file')
        with page.expect_file_chooser(timeout=5000) as chooser:
            control.locator.click()
        chooser.value.set_files(str(image))
        trace.complete('Set file', 'File chooser accepted one selection')

    trace.start('Confirm file input received a file')
    if not control.direct_input:
        trace.complete('Confirm file input received a file', 'File chooser selection completed')
        return
    deadline = monotonic() + 2
    while True:
        attached, _, _, files = _input_state(control.locator)
        same_node = _same_input_node(control.locator)
        events = _upload_events(control.locator)
        event_note = _upload_event_note(events)
        if same_node and attached and files == 1 and events.input_events and events.change_events:
            trace.complete(
                'Confirm file input received a file',
                'Browser input reports one selected file; ' + event_note,
            )
            return
        scope = control.scope
        progress = bool(scope is not None and _upload_progress(scope))
        preview = bool(scope is not None and _thumbnail_preview_count(scope))
        if not same_node and events.input_events and events.change_events and (
                progress or preview or _replacement_input_present(scope, control.locator)):
            trace.complete(
                'Confirm file input received a file',
                'Original input was replaced after selection; fresh thumbnail UI evidence appeared; '
                + event_note,
            )
            return
        if monotonic() >= deadline:
            raise BrowserSessionError(
                'Browser-side evidence did not confirm input/change handling after file selection. '
                + event_note
            )
        page.wait_for_timeout(100)


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
    timeout: float = 30, on_step=None, trace: UploadTrace | None = None,
) -> None:
    """Require cover appearance, preserved content, and a fresh autosave signal."""
    deadline = monotonic() + timeout
    saw_unsaved = not previously_saved
    saw_cover = False
    progress_checked = False
    reported_late_events = False
    if trace is not None:
        trace.start('Detect upload/progress state')
    while True:
        monitor.require_clear(page)
        _require_same_content(
            page, title_field, expected_title, body_surface, body_text,
            publication_url, draft_url,
        )
        inspection = inspect_cover(page)
        if trace is not None and not progress_checked:
            trace.complete(
                'Detect upload/progress state',
                'Upload/progress indicator present' if inspection.diagnostics.upload_progress_present
                else 'No upload/progress indicator observed; preview polling continued',
            )
            trace.start('Detect thumbnail preview')
            progress_checked = True
        if trace is not None:
            events = _upload_events(page)
            if (events.file_reader_started or events.processing_appeared
                    or events.preview_mutated or events.input_replaced):
                note = 'Latest uploader reaction: ' + _upload_event_note(events)
                if note not in trace.observations:
                    trace.observations.append(note)
                reported_late_events = True
        saw_cover = saw_cover or inspection.state == CoverState.PRESENT
        if saw_cover:
            if trace is not None:
                trace.complete('Detect thumbnail preview', 'Thumbnail preview detected after fresh DOM query')
                trace.start('Detect fresh Saved state')
            if on_step is not None:
                on_step('Detect fresh Saved state')
        saved = editor.save_visible(page)
        saving = _saving_visible(page)
        saw_unsaved = saw_unsaved or not saved or saving
        if saw_cover and saved and not saving and saw_unsaved:
            monitor.require_clear(page)
            if trace is not None:
                trace.complete('Detect fresh Saved state', 'Fresh Saved state confirmed')
                trace.start('Verify title/body preserved')
            _require_same_content(
                page, title_field, expected_title, body_surface, body_text,
                publication_url, draft_url,
            )
            if trace is not None:
                trace.complete('Verify title/body preserved', 'Title and body remained unchanged')
            return
        if monotonic() >= deadline:
            if trace is not None and not reported_late_events:
                note = 'Latest uploader reaction: ' + _upload_event_note(_upload_events(page))
                if note not in trace.observations:
                    trace.observations.append(note)
            if not saw_cover:
                raise BrowserSessionError('The uploaded cover image did not appear in the editor.')
            raise BrowserSessionError('A fresh Saved signal did not confirm the cover image upload.')
        page.wait_for_timeout(200)
