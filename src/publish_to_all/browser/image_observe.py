"""Inspect and edit only an article's Social Preview image setting."""

from dataclasses import dataclass, field
from enum import StrEnum
import re
from threading import Event, Thread
from time import monotonic

from playwright.sync_api import Error as PlaywrightError

from ..errors import BrowserSessionError


class ImageFeature(StrEnum):
    """Image features which must not be treated as interchangeable."""

    SOCIAL_POST_PREVIEW = 'social/post preview image'
    ATTACHMENT_THUMBNAIL = 'file-attachment thumbnail'
    INLINE_BODY = 'inline body image'
    PUBLICATION_COVER = 'publication cover image'
    POST_HERO = 'post hero image'
    UNKNOWN = 'unknown image control'


class SocialPreviewState(StrEnum):
    NONE = 'none'
    PRESENT = 'present'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class SocialPreviewTarget:
    dialog: object
    file_input: object
    select_control: object


@dataclass(frozen=True)
class SocialPreviewDiagnostics:
    image_label_count: int = 0
    image_input_count: int = 0
    select_control_count: int = 0
    replace_control_count: int = 0
    preview_count: int = 0
    rejected_control_count: int = 0
    local_save_count: int = 0
    local_save_enabled: bool = False


@dataclass(frozen=True)
class SocialPreviewInspection:
    state: SocialPreviewState
    target: SocialPreviewTarget | None = None
    diagnostics: SocialPreviewDiagnostics = SocialPreviewDiagnostics()


@dataclass
class UploadTrace:
    """Fixed-vocabulary checkpoints safe to store in local diagnostics."""

    completed: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    transition_diagnostics: tuple[str, ...] = ()
    current: str = 'Open Post settings'

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
        result = f'Completed checkpoints: {completed}\nSafe observations: {observations}'
        if self.transition_diagnostics:
            result += '\n' + '\n'.join(self.transition_diagnostics)
        return result

    def record_transition(self, observation) -> None:
        """Store only fixed-vocabulary booleans from the upload transition."""
        answer = lambda value: 'Yes' if value else 'No'
        self.transition_diagnostics = (
            f'Original input detached: {answer(observation.original_input_replaced)}',
            f'Replacement input found: {answer(observation.different_input_appeared)}',
            f'Social Preview panel rerendered: {answer(observation.social_preview_rerendered)}',
            f'Processing detected: {answer(observation.processing_appeared)}',
            f'Preview mutation detected: {answer(observation.preview_appeared)}',
            f'Final preview detected: {answer(observation.final_preview_detected)}',
            f'Social Preview dialog replaced: {answer(observation.dialog_replaced)}',
            f'Social Preview dialog temporarily closed: '
            f'{answer(observation.dialog_temporarily_closed)}',
        )


@dataclass(frozen=True)
class DomObservation:
    input_events: int = 0
    change_events: int = 0
    original_input_received_file: bool = False
    different_input_received_file: bool = False
    different_input_appeared: bool = False
    original_input_replaced: bool = False
    added_nodes: int = 0
    removed_nodes: int = 0
    processing_appeared: bool = False
    preview_appeared: bool = False
    social_preview_rerendered: bool = False
    active_control_changed: bool = False
    saving_appeared: bool = False
    saved_appeared: bool = False
    saving_then_saved: bool = False
    observed_input_count: int = 1
    dialog_replaced: bool = False
    dialog_temporarily_closed: bool = False
    final_preview_detected: bool = False


@dataclass
class SafeNetworkObservation:
    """Count only trusted response classes and statuses; retain no URLs or headers."""

    publication_url: str
    active: bool = False
    responses: int = 0
    successful_responses: int = 0
    failed_responses: int = 0

    def observe(self, response) -> None:
        if not self.active:
            return
        try:
            from .substack import trusted_page
            resource_type = response.request.resource_type
            if resource_type not in {'xhr', 'fetch', 'image'}:
                return
            if not trusted_page(response.url, self.publication_url):
                return
            self.responses += 1
            if 200 <= response.status < 400:
                self.successful_responses += 1
            elif response.status >= 400:
                self.failed_responses += 1
        except (AttributeError, TypeError, ValueError):
            return


SETTINGS = re.compile(r'^Settings$', re.I)
POST_SETTINGS = re.compile(r'^Post settings$', re.I)
SOCIAL_PREVIEW = re.compile(r'^Social preview$', re.I)
EDIT = re.compile(r'^Edit$', re.I)
EDIT_SOCIAL_PREVIEW = re.compile(r'^Edit social preview$', re.I)
IMAGE = re.compile(r'^Image$', re.I)
SELECT_IMAGE = re.compile(r'^Select image$', re.I)
UPLOAD_FILE = re.compile(r'^Upload file$', re.I)
REMOVE_IMAGE = re.compile(r'^Remove image$', re.I)
REPLACE_IMAGE = re.compile(r'^(?:Change|Replace)(?: image)?$', re.I)
LOCAL_SAVE = re.compile(r'^(?:Save|Done)$', re.I)
FORBIDDEN_ACTION = re.compile(r'^(?:Continue|Publish(?: post)?|Send(?: now)?|Schedule)$', re.I)
REJECTED_CONTEXT = re.compile(
    r'(?:file settings|thumbnail|insert image|publication (?:cover|logo)|attachment)', re.I,
)
PREVIEW_SELECTOR = (
    '[data-testid*="social" i] img, [data-testid*="preview" i] img, '
    '[class*="social-preview" i] img, [class*="image-preview" i] img, picture img'
)


def _visible(items):
    return [item for item in items if item.is_visible()]


def _controls(scope, role: str, name):
    return _visible(scope.get_by_role(role, name=name).all())


def _enabled(items):
    enabled = []
    for item in items:
        try:
            if item.is_enabled():
                enabled.append(item)
        except (PlaywrightError, AttributeError, TypeError):
            continue
    return enabled


def _wait_for_candidates(page, monitor, finder, *, description: str,
                         timeout_ms: int = 10000):
    """Wait through hydration/rerenders for one visible, enabled semantic target."""
    deadline = monotonic() + timeout_ms / 1000
    while monotonic() < deadline:
        monitor.require_clear(page)
        try:
            candidates = finder()
        except (PlaywrightError, AttributeError, TypeError):
            candidates = []
        if len(candidates) > 1:
            raise BrowserSessionError(f'{description} was ambiguous.')
        if len(candidates) == 1:
            return candidates[0]
        page.wait_for_timeout(100)
    monitor.require_clear(page)
    raise BrowserSessionError(f'{description} could not be identified before the bounded wait expired.')


def _post_settings_controls(page):
    """Use the editor-specific test id to disambiguate generic Settings controls."""
    generic = _controls(page, 'button', SETTINGS)
    return _enabled([
        control for control in generic
        if control.get_attribute('data-testid') == 'settings-button'
    ])


def _post_settings_dialogs(page):
    dialogs = _visible(page.locator('[role="dialog"][data-testid="settings-modal"]').all())
    return [dialog for dialog in dialogs if len(_controls(dialog, 'heading', POST_SETTINGS)) == 1]


def _social_preview_dialogs(page):
    dialogs = _visible(page.locator('[role="dialog"][data-testid="modal"]').all())
    return [
        dialog for dialog in dialogs
        if len(_controls(dialog, 'heading', EDIT_SOCIAL_PREVIEW)) == 1
    ]


def classify_image_feature(*, panel_heading: str = '', label: str = '', in_body: bool = False,
                           publication_editor: bool = False) -> ImageFeature:
    """Classify controls from explicit rendered context, never from the word image alone."""
    context = ' '.join((panel_heading, label)).strip().lower()
    if in_body:
        return ImageFeature.INLINE_BODY
    if publication_editor and 'cover' in context:
        return ImageFeature.PUBLICATION_COVER
    if 'file settings' in context and 'thumbnail' in context:
        return ImageFeature.ATTACHMENT_THUMBNAIL
    if 'social preview' in context or 'post preview' in context:
        return ImageFeature.SOCIAL_POST_PREVIEW
    if re.search(r'\b(?:post )?(?:hero|cover) image\b', context):
        return ImageFeature.POST_HERO
    return ImageFeature.UNKNOWN


def _safe_text(scope) -> str:
    try:
        return ' '.join(scope.inner_text().split())[:500]
    except (PlaywrightError, AttributeError, TypeError):
        return ''


def _social_preview_section(settings_dialog):
    """Return one settings row containing the exact label and its own Edit button."""
    labels = _visible(settings_dialog.get_by_text(SOCIAL_PREVIEW, exact=True).all())
    if len(labels) != 1:
        raise BrowserSessionError('The Social preview section could not be identified.')
    label = labels[0]
    for selector in (
        'xpath=ancestor::*[@data-testid][1]',
        'xpath=ancestor::section[1]',
        'xpath=ancestor::li[1]',
        'xpath=ancestor::div[.//button][1]',
    ):
        try:
            candidate = label.locator(selector)
            if candidate.count() == 1 and candidate.is_visible():
                edits = _enabled(_controls(candidate, 'button', EDIT))
                if len(edits) == 1 and not REJECTED_CONTEXT.search(_safe_text(candidate)):
                    return candidate, edits[0]
        except (PlaywrightError, AttributeError, TypeError):
            continue
    raise BrowserSessionError('The Social preview Edit control could not be identified.')


def _social_preview_edit_controls(settings_dialog):
    """Return no control while loading, and reject duplicate labels immediately."""
    labels = _visible(settings_dialog.get_by_text(SOCIAL_PREVIEW, exact=True).all())
    if len(labels) > 1:
        raise BrowserSessionError('The Social preview section was ambiguous.')
    if not labels:
        return []
    try:
        return [_social_preview_section(settings_dialog)[1]]
    except BrowserSessionError:
        return []


def open_social_preview_editor(page, monitor, *, trace: UploadTrace | None = None):
    """Open Post settings and the exact article-level Social Preview editor."""
    monitor.require_clear(page)
    if trace:
        trace.start('Open Post settings')
    settings = _wait_for_candidates(
        page, monitor, lambda: _post_settings_controls(page),
        description='The editor Post settings control',
    )
    settings.click()
    monitor.require_clear(page)
    if trace:
        trace.complete('Open Post settings')

    settings_dialog = _wait_for_candidates(
        page, monitor, lambda: _post_settings_dialogs(page),
        description='The Post settings dialog',
    )
    if trace:
        trace.start('Open Social preview')
    edit = _wait_for_candidates(
        page, monitor, lambda: _social_preview_edit_controls(settings_dialog),
        description='The Social preview Edit control',
    )
    edit.click()
    monitor.require_clear(page)
    if trace:
        trace.complete('Open Social preview')
        trace.start('Open Edit social preview')

    dialog = _wait_for_candidates(
        page, monitor, lambda: _social_preview_dialogs(page),
        description='The Edit social preview dialog',
    )
    if trace:
        trace.complete('Open Edit social preview')
    return dialog


def _social_preview_image_scope(dialog):
    """Choose the narrowest visible field container carrying exact Image controls."""
    labels = _visible(dialog.get_by_text(IMAGE, exact=True).all())
    if len(labels) != 1:
        return None
    label = labels[0]
    for selector in (
        'xpath=ancestor::div[.//input[@type="file"]][1]',
        'xpath=ancestor::div[.//button[normalize-space()="Select image" '
        'or normalize-space()="Change" or normalize-space()="Change image" '
        'or normalize-space()="Replace" or normalize-space()="Replace image"]][1]',
        'xpath=ancestor::*[@data-testid][1]',
        'xpath=ancestor::section[1]',
        'xpath=ancestor::li[1]',
    ):
        try:
            candidate = label.locator(selector)
            if candidate.count() != 1 or not candidate.is_visible():
                continue
            has_input = candidate.locator('input[type="file"]').count() > 0
            has_control = bool(_controls(candidate, 'button', REMOVE_IMAGE))
            has_preview = candidate.locator(PREVIEW_SELECTOR).count() > 0
            if has_input or has_control or has_preview:
                return candidate
        except (PlaywrightError, AttributeError, TypeError):
            continue
    # The exact dialog heading and exact Image label still provide a bounded
    # fallback when Substack omits a semantic wrapper around its only image field.
    return dialog


def inspect_social_preview_image(page, monitor, *, trace: UploadTrace | None = None
                                 ) -> SocialPreviewInspection:
    """Classify only the Image field inside Edit social preview."""
    dialog = open_social_preview_editor(page, monitor, trace=trace)
    if trace:
        trace.start('Locate Social Preview image control')
    if REJECTED_CONTEXT.search(_safe_text(dialog)):
        return SocialPreviewInspection(
            SocialPreviewState.UNKNOWN,
            diagnostics=SocialPreviewDiagnostics(rejected_control_count=1),
        )
    scope = _social_preview_image_scope(dialog)
    if scope is None:
        return SocialPreviewInspection(SocialPreviewState.UNKNOWN)
    context = _safe_text(scope)
    if REJECTED_CONTEXT.search(context):
        return SocialPreviewInspection(
            SocialPreviewState.UNKNOWN,
            diagnostics=SocialPreviewDiagnostics(rejected_control_count=1),
        )
    inputs = [candidate for candidate in scope.locator('input[type="file"]').all()
              if 'image/' in (candidate.get_attribute('accept') or '').lower()]
    uploads = [candidate for candidate in inputs
               if (candidate.get_attribute('aria-label') or '').strip().lower() == 'upload file']
    removals = _controls(scope, 'button', REMOVE_IMAGE)
    replacements = _controls(scope, 'button', REPLACE_IMAGE)
    previews = _visible(scope.locator(PREVIEW_SELECTOR).all()) if removals else []
    local_saves = _controls(dialog, 'button', LOCAL_SAVE)
    diagnostics = SocialPreviewDiagnostics(
        image_label_count=1, image_input_count=len(inputs),
        select_control_count=len(uploads),
        replace_control_count=len(removals) + len(replacements),
        preview_count=len(previews),
        local_save_count=len(local_saves),
        local_save_enabled=bool(_enabled(local_saves)),
    )
    if len(inputs) == 1 and len(uploads) == 1 and not removals and not replacements:
        target = SocialPreviewTarget(dialog, inputs[0], uploads[0])
        if trace:
            trace.complete('Locate Social Preview image control', 'Empty Social Preview Image field')
        return SocialPreviewInspection(SocialPreviewState.NONE, target, diagnostics)
    if len(inputs) == 1 and len(uploads) == 1 and len(removals) == 1 and not replacements:
        if trace:
            trace.complete('Locate Social Preview image control', 'Existing Social Preview image')
        return SocialPreviewInspection(SocialPreviewState.PRESENT, diagnostics=diagnostics)
    return SocialPreviewInspection(SocialPreviewState.UNKNOWN, diagnostics=diagnostics)


def open_social_preview_image(page, monitor) -> SocialPreviewTarget:
    """Compatibility helper for the manual observer; require an empty Social Preview field."""
    inspection = inspect_social_preview_image(page, monitor)
    if inspection.state != SocialPreviewState.NONE or inspection.target is None:
        raise BrowserSessionError('The empty Social preview image chooser is ambiguous.')
    return inspection.target


def install_dom_observer(page, target: SocialPreviewTarget) -> None:
    """Install safe counters. File names, bytes, values, and request data are never read."""
    target.file_input.evaluate(
        """node => {
            const dialog = node.closest('[role="dialog"]');
            const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
            const isSocialDialog = item => item.matches('[role="dialog"][data-testid="modal"]')
                && [...item.querySelectorAll('[role="heading"], h1, h2, h3, h4, h5, h6')]
                    .some(heading => /^edit social preview$/i.test(normalize(heading.textContent)));
            const socialDialogs = () => [...document.querySelectorAll(
                '[role="dialog"][data-testid="modal"]'
            )].filter(isSocialDialog);
            const resolveDialog = () => {
                const dialogs = socialDialogs();
                return dialogs.length === 1 ? dialogs[0] : null;
            };
            const fileInputs = scope => scope
                ? [...scope.querySelectorAll('input[type="file"]')]
                : [];
            const initialInputs = new Set(fileInputs(dialog));
            initialInputs.add(node);
            const previewSelector = 'img, picture, [data-testid*="preview" i] '
                + '[style*="background-image" i], [class*="preview" i] '
                + '[style*="background-image" i]';
            const progressSelector = 'progress, [role="progressbar"], [aria-busy="true"], '
                + '[class*="uploading" i], [class*="processing" i], '
                + '[data-testid*="upload-progress" i]';
            const visible = item => !!(item.offsetWidth || item.offsetHeight
                || item.getClientRects().length);
            const resolveImageScope = currentDialog => {
                if (!currentDialog) return null;
                const labels = [...currentDialog.querySelectorAll('*')].filter(item =>
                    /^image$/i.test(normalize(item.textContent))
                    && ![...item.children].some(child => /^image$/i.test(normalize(child.textContent))));
                if (labels.length !== 1) return currentDialog;
                let candidate = labels[0];
                while (candidate && candidate !== currentDialog) {
                    if (candidate.querySelector('input[type="file"], ' + previewSelector)) {
                        return candidate;
                    }
                    candidate = candidate.parentElement;
                }
                return currentDialog;
            };
            const panel = resolveImageScope(dialog) || node.parentElement;
            const state = {
                original: node, dialog, panel, initialInputs, inputEvents: 0, changeEvents: 0,
                originalReceived: false, differentReceived: false,
                differentAppeared: false, originalReplaced: false,
                addedNodes: 0, removedNodes: 0, processingAppeared: false,
                previewAppeared: false, rerendered: false, dialogReplaced: false,
                dialogTemporarilyClosed: false, finalPreview: false,
                initialPreviews: new Set((panel || dialog).querySelectorAll(previewSelector)),
                savingAppeared: false, savedAppeared: false, saveSequence: [],
                initialActive: document.activeElement, activeChanged: false
            };
            state.refresh = records => {
                const currentDialog = resolveDialog();
                const currentPanel = resolveImageScope(currentDialog);
                const originalDialogDetached = !state.dialog.isConnected;
                state.originalReplaced ||= !state.original.isConnected;
                state.dialogReplaced ||= originalDialogDetached
                    && !!currentDialog && currentDialog !== state.dialog;
                state.dialogTemporarilyClosed ||= originalDialogDetached && !currentDialog;
                state.rerendered ||= !state.panel?.isConnected
                    || (!!currentPanel && currentPanel !== state.panel);
                const scope = currentPanel || currentDialog;
                const inputs = fileInputs(scope);
                state.differentAppeared ||= inputs.some(input => !state.initialInputs.has(input));
                state.processingAppeared ||= !!scope?.querySelector(progressSelector);
                const currentPreviews = scope
                    ? [...scope.querySelectorAll(previewSelector)].filter(visible) : [];
                const newPreview = currentPreviews.some(preview => !state.initialPreviews.has(preview));
                state.previewAppeared ||= newPreview;
                state.activeChanged ||= document.activeElement !== state.initialActive;
                for (const record of records || []) {
                    const changed = record.target.nodeType === 1
                        ? record.target : record.target.parentElement;
                    const inScope = !!changed && !!scope
                        && (scope === changed || scope.contains(changed));
                    state.previewAppeared ||= inScope && (
                        changed.matches?.(previewSelector)
                        || !!changed.closest?.(previewSelector)
                        || !!changed.querySelector?.(previewSelector)
                    );
                }
                state.finalPreview ||= newPreview
                    || (state.previewAppeared && currentPreviews.length > 0);
                return {currentDialog, scope, inputs};
            };
            window.__publishToAllManualImageObservation = state;
            const observeFileEvent = (event, kind) => {
                const current = window.__publishToAllManualImageObservation;
                const target = event.target;
                if (!current || !(target instanceof HTMLInputElement) || target.type !== 'file') return;
                if (kind === 'input') current.inputEvents += 1;
                if (kind === 'change') current.changeEvents += 1;
                const received = !!target.files && target.files.length > 0;
                if (target === current.original) current.originalReceived ||= received;
                else current.differentReceived ||= received;
            };
            document.addEventListener('input', event => observeFileEvent(event, 'input'), true);
            document.addEventListener('change', event => observeFileEvent(event, 'change'), true);
            document.addEventListener('focusin', () => {
                const current = window.__publishToAllManualImageObservation;
                if (current && document.activeElement !== current.initialActive) current.activeChanged = true;
            }, true);
            state.observer = new MutationObserver(records => {
                const current = window.__publishToAllManualImageObservation;
                if (!current) return;
                for (const record of records) {
                    current.addedNodes += record.addedNodes.length;
                    current.removedNodes += record.removedNodes.length;
                }
                const refreshed = current.refresh(records);
                const scopes = refreshed.scope ? [refreshed.scope] : [];
                for (const record of records) {
                    const changed = record.target.nodeType === 1
                        ? record.target : record.target.parentElement;
                    const inSocialScope = !!changed && scopes.some(scope =>
                        scope === changed || scope.contains(changed));
                    current.previewAppeared ||= inSocialScope && (
                        changed.matches?.(previewSelector)
                        || !!changed.closest?.(previewSelector)
                        || !!changed.querySelector?.(previewSelector)
                    );
                    for (const added of record.addedNodes) {
                        if (added.nodeType !== 1) continue;
                        const addedInScope = scopes.some(scope =>
                            scope === added || scope.contains(added) || added.contains(scope));
                        current.processingAppeared ||= addedInScope && (
                            added.matches?.(progressSelector)
                            || !!added.querySelector?.(progressSelector)
                        );
                        current.previewAppeared ||= addedInScope && (
                            added.matches?.(previewSelector)
                            || !!added.querySelector?.(previewSelector)
                        );
                    }
                    const statusNodes = [...record.addedNodes];
                    if (record.type === 'characterData') statusNodes.push(record.target);
                    for (const added of statusNodes) {
                        const element = added.nodeType === 1 ? added : added.parentElement;
                        const containingDialog = element?.closest?.('[role="dialog"]');
                        if (containingDialog && isSocialDialog(containingDialog)) continue;
                        const text = (added.textContent || '').trim();
                        const saving = /^(?:Saving|Saving changes)(?:\\.{3}|…)?$/i.test(text);
                        const saved = /^(?:Saved|Draft saved|All changes saved|Saved to drafts)$/i.test(text);
                        current.savingAppeared ||= saving;
                        current.savedAppeared ||= saved;
                        if (saving && current.saveSequence.at(-1) !== 'saving') current.saveSequence.push('saving');
                        if (saved && current.saveSequence.at(-1) !== 'saved') current.saveSequence.push('saved');
                    }
                }
            });
            state.observer.observe(document.documentElement, {
                subtree: true, childList: true, attributes: true, characterData: true
            });
        }"""
    )


def read_dom_observation(page) -> DomObservation:
    try:
        value = page.evaluate(
            """() => {
                const s = window.__publishToAllManualImageObservation;
                if (!s) return {};
                const refreshed = s.refresh([]);
                return {
                    inputEvents: s.inputEvents, changeEvents: s.changeEvents,
                    originalReceived: s.originalReceived,
                    differentReceived: s.differentReceived,
                    differentAppeared: s.differentAppeared,
                    originalReplaced: s.originalReplaced || !s.original.isConnected,
                    addedNodes: s.addedNodes, removedNodes: s.removedNodes,
                    processingAppeared: s.processingAppeared,
                    previewAppeared: s.previewAppeared,
                    rerendered: s.rerendered,
                    dialogReplaced: s.dialogReplaced,
                    dialogTemporarilyClosed: s.dialogTemporarilyClosed,
                    finalPreview: s.finalPreview,
                    activeChanged: s.activeChanged,
                    savingAppeared: s.savingAppeared,
                    savedAppeared: s.savedAppeared,
                    savingThenSaved: s.saveSequence.some((value, index) =>
                        value === 'saving' && s.saveSequence.slice(index + 1).includes('saved')),
                    inputCount: refreshed.inputs.length
                };
            }"""
        )
        return DomObservation(
            input_events=int(value.get('inputEvents') or 0),
            change_events=int(value.get('changeEvents') or 0),
            original_input_received_file=bool(value.get('originalReceived')),
            different_input_received_file=bool(value.get('differentReceived')),
            different_input_appeared=bool(value.get('differentAppeared')),
            original_input_replaced=bool(value.get('originalReplaced')),
            added_nodes=int(value.get('addedNodes') or 0),
            removed_nodes=int(value.get('removedNodes') or 0),
            processing_appeared=bool(value.get('processingAppeared')),
            preview_appeared=bool(value.get('previewAppeared')),
            social_preview_rerendered=bool(value.get('rerendered')),
            active_control_changed=bool(value.get('activeChanged')),
            saving_appeared=bool(value.get('savingAppeared')),
            saved_appeared=bool(value.get('savedAppeared')),
            saving_then_saved=bool(value.get('savingThenSaved')),
            observed_input_count=int(value.get('inputCount') or 0),
            dialog_replaced=bool(value.get('dialogReplaced')),
            dialog_temporarily_closed=bool(value.get('dialogTemporarilyClosed')),
            final_preview_detected=bool(value.get('finalPreview')),
        )
    except (PlaywrightError, AttributeError, TypeError, ValueError):
        return DomObservation(observed_input_count=0)


def reset_save_observation(page) -> None:
    page.evaluate(
        """() => {
            const s = window.__publishToAllManualImageObservation;
            if (!s) throw new Error('Social Preview observer missing');
            s.savingAppeared = false;
            s.savedAppeared = false;
            s.saveSequence = [];
        }"""
    )


def _wait_for(page, monitor, predicate, *, timeout_ms: int = 15000) -> DomObservation:
    deadline = monotonic() + timeout_ms / 1000
    latest = DomObservation(observed_input_count=0)
    while monotonic() < deadline:
        monitor.require_clear(page)
        latest = read_dom_observation(page)
        if predicate(latest):
            return latest
        page.wait_for_timeout(100)
    monitor.require_clear(page)
    return latest


def supply_social_preview_file(page, target: SocialPreviewTarget, image_path, monitor,
                               *, trace: UploadTrace, before_selection=None) -> DomObservation:
    """Supply one file once and require the transition observed in the manual run."""
    trace.start('Supply file')
    install_dom_observer(page, target)
    monitor.require_clear(page)
    if before_selection is not None:
        before_selection()
    target.file_input.set_input_files(str(image_path))
    trace.complete('Supply file', 'One set_input_files call completed')

    trace.start('Observe input/change')
    events = _wait_for(
        page, monitor, lambda value: value.input_events >= 1 and value.change_events >= 1,
    )
    trace.record_transition(events)
    if events.input_events != 1 or events.change_events != 1:
        raise BrowserSessionError('The Social Preview input/change event evidence was not exact.')
    trace.complete('Observe input/change', 'One input event and one change event')

    trace.start('Detect upload transition')
    preview = _wait_for(page, monitor, lambda value: value.final_preview_detected)
    trace.record_transition(preview)
    if not preview.final_preview_detected:
        raise BrowserSessionError('The Social Preview image preview did not appear.')
    trace.complete('Detect upload transition', 'Bounded upload transition evidence accepted')
    trace.start('Detect preview')
    trace.complete('Detect preview', 'Social Preview image preview appeared')
    return preview


def _current_social_preview_dialog(page):
    dialogs = _social_preview_dialogs(page)
    return dialogs[0] if len(dialogs) == 1 else None


def save_social_preview(page, monitor, *, trace: UploadTrace) -> DomObservation:
    """Click only the Save/Done control inside the Social Preview dialog."""
    trace.start('Locate Social Preview Save')
    dialog = _current_social_preview_dialog(page)
    if dialog is None:
        raise BrowserSessionError('The Social Preview editor closed before its local Save was found.')
    controls = _controls(dialog, 'button', LOCAL_SAVE)
    forbidden = _controls(dialog, 'button', FORBIDDEN_ACTION)
    if len(controls) != 1 or forbidden:
        raise BrowserSessionError('The local Social Preview Save control was ambiguous.')
    trace.complete('Locate Social Preview Save')
    reset_save_observation(page)
    trace.start('Save Social Preview')
    controls[0].click()
    monitor.require_clear(page)
    trace.complete('Save Social Preview')

    trace.start('Confirm Saving → Saved')
    result = _wait_for(page, monitor, lambda value: value.saving_then_saved)
    if not result.saving_then_saved:
        raise BrowserSessionError('A fresh Saving → Saved transition was not observed.')
    trace.complete('Confirm Saving → Saved')
    return result


def wait_for_manual_upload(page, monitor, *, reader=input) -> None:
    """Keep Playwright pumping while a terminal reader waits, checking rate limits promptly."""
    finished = Event()
    failure = []

    def wait_for_enter():
        try:
            reader('After you finish the manual image upload and save it, press Enter here: ')
        except BaseException as exc:  # propagated on the browser-owning thread
            failure.append(exc)
        finally:
            finished.set()

    Thread(target=wait_for_enter, daemon=True).start()
    while not finished.is_set():
        monitor.require_clear(page)
        page.wait_for_timeout(100)
    monitor.require_clear(page)
    if failure:
        raise failure[0]
