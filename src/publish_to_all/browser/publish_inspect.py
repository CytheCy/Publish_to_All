"""Read the final Substack publication UI while making publication impossible."""

from dataclasses import dataclass
import re
from time import monotonic
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from ..errors import BrowserSessionError
from .substack import trusted_page


FINAL_ACTION = re.compile(
    r'^(?:Publish|Send|Schedule)\b.*$', re.I,
)
SAFE_EXIT = re.compile(r'^(?:Back|Cancel|Close)$', re.I)
SENSITIVE = re.compile(r'(?:[\w.+-]+@[\w.-]+|(?:token|password|secret)\s*[=:])', re.I)


@dataclass(frozen=True)
class ContinueControl:
    locator: object
    role: str
    label: str
    test_id: str
    element_type: str
    context: str


@dataclass(frozen=True)
class ContinueCandidateEvidence:
    tag: str
    role: str
    accessible_name: str
    visible_text: str
    aria_label: str
    title: str
    test_id: str
    element_type: str
    href: str
    visible: bool
    enabled: bool
    in_dialog: bool
    in_editor: bool
    appears_submit: bool
    form_attributes: str
    ancestor_context: str
    nearby_labels: tuple[str, ...]


@dataclass(frozen=True)
class ContinueDiagnostic:
    candidates: tuple[ContinueCandidateEvidence, ...]

    @property
    def visible_enabled_count(self) -> int:
        return sum(item.visible and item.enabled for item in self.candidates)


@dataclass(frozen=True)
class PublicationControl:
    label: str
    role: str
    control_type: str
    value: str
    required: bool
    optional: bool
    final_action: bool


@dataclass(frozen=True)
class FinalScreenInspection:
    url: str
    title: str
    controls: tuple[PublicationControl, ...]
    final_actions: tuple[str, ...]
    safe_exit: str | None
    exit_behavior: str
    blocked_mutation_methods: tuple[str, ...] = ()


@dataclass
class MutationGuard:
    """Abort all mutation transports after Continue is selected."""

    blocked_methods: list[str]

    def handle(self, route) -> None:
        method = (route.request.method or '').upper()
        if method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            if method not in self.blocked_methods:
                self.blocked_methods.append(method)
            route.abort()
        else:
            route.continue_()


def install_mutation_guard(page) -> MutationGuard:
    guard = MutationGuard([])
    page.route('**/*', guard.handle)
    return guard


def _visible_enabled(items) -> list:
    result = []
    for item in items:
        try:
            if item.is_visible() and item.is_enabled():
                result.append(item)
        except (PlaywrightError, AttributeError, TypeError):
            continue
    return result


def inspect_continue_candidates(page) -> ContinueDiagnostic:
    """Read safe DOM semantics for exact accessible-name Continue buttons; never click."""
    candidates = page.get_by_role('button', name='Continue', exact=True).all()
    evidence = []
    for control in candidates:
        try:
            raw = control.evaluate(
                """node => {
                    const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                    const labelledBy = clean(node.getAttribute('aria-labelledby'));
                    const labelledText = labelledBy ? labelledBy.split(/\\s+/).map(id =>
                        clean(document.getElementById(id)?.innerText ||
                              document.getElementById(id)?.textContent)).filter(Boolean).join(' ') : '';
                    const accessibleName = clean(node.getAttribute('aria-label')) || labelledText ||
                        clean(node.innerText) || clean(node.value) || clean(node.getAttribute('title'));
                    const style = getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    const visible = style.visibility !== 'hidden' && style.display !== 'none' &&
                        (rect.width > 0 || rect.height > 0);
                    const disabled = !!node.disabled || node.getAttribute('aria-disabled') === 'true';
                    const form = node.form || node.closest('form');
                    const safeAttrs = element => {
                        if (!element) return '';
                        const attrs = [
                            'id', 'class', 'role', 'aria-label', 'title', 'data-testid',
                            'name', 'method'
                        ];
                        return element.tagName.toLowerCase() + attrs.map(name => {
                            const value = clean(element.getAttribute(name));
                            return value ? ` ${name}=${JSON.stringify(value)}` : '';
                        }).join('');
                    };
                    const ancestors = [];
                    for (let item = node.parentElement; item && ancestors.length < 8;
                            item = item.parentElement) {
                        ancestors.push(safeAttrs(item));
                    }
                    const nearby = [];
                    let area = node.parentElement;
                    for (let depth = 0; area && depth < 5; depth++, area = area.parentElement) {
                        if (area.querySelectorAll(
                                'button, [role="button"], a[href], h1, h2, h3, label, legend'
                            ).length > 1) break;
                    }
                    if (area) {
                        for (const item of area.querySelectorAll(
                                'h1, h2, h3, label, legend, button, [role="button"], [aria-label]')) {
                            if (item === node) continue;
                            const label = clean(item.getAttribute('aria-label')) || clean(item.innerText);
                            if (label && label.length <= 80 && !nearby.includes(label)) nearby.push(label);
                            if (nearby.length === 8) break;
                        }
                    }
                    const typeAttr = clean(node.getAttribute('type')).toLowerCase();
                    const effectiveType = node.tagName === 'BUTTON' ? (typeAttr || 'submit') : typeAttr;
                    return {
                        tag: node.tagName.toLowerCase(),
                        role: clean(node.getAttribute('role')) ||
                            (node.tagName === 'BUTTON' ? 'button' : ''),
                        accessibleName, text: clean(node.innerText),
                        ariaLabel: clean(node.getAttribute('aria-label')),
                        title: clean(node.getAttribute('title')),
                        testId: clean(node.getAttribute('data-testid')),
                        type: typeAttr, href: clean(node.getAttribute('href')),
                        visible, enabled: !disabled,
                        inDialog: !!node.closest('[role="dialog"]'),
                        inEditor: !!node.closest('.editor.newsletter-post-editor'),
                        appearsSubmit: effectiveType === 'submit' || !!node.getAttribute('formaction'),
                        form: safeAttrs(form), ancestors, nearby
                    };
                }"""
            )
        except (PlaywrightError, AttributeError, TypeError):
            continue
        safe_href = ''
        raw_href = str(raw.get('href') or '')
        if raw_href and not re.match(r'^(?:javascript:|data:)', raw_href, re.I):
            parsed = urlsplit(raw_href)
            safe_href = parsed.path or raw_href if not parsed.query and not parsed.fragment else parsed.path
        evidence.append(ContinueCandidateEvidence(
            tag=_safe_text(raw.get('tag'), '[unknown]'),
            role=_safe_text(raw.get('role'), '[none]'),
            accessible_name=_safe_text(raw.get('accessibleName'), '[empty]'),
            visible_text=_safe_text(raw.get('text'), '[empty]'),
            aria_label=_safe_text(raw.get('ariaLabel'), '[none]'),
            title=_safe_text(raw.get('title'), '[none]'),
            test_id=_safe_text(raw.get('testId'), '[none]'),
            element_type=_safe_text(raw.get('type'), '[not set]'),
            href=_safe_text(safe_href, '[none]'),
            visible=bool(raw.get('visible')), enabled=bool(raw.get('enabled')),
            in_dialog=bool(raw.get('inDialog')), in_editor=bool(raw.get('inEditor')),
            appears_submit=bool(raw.get('appearsSubmit')),
            form_attributes=_safe_text(raw.get('form'), '[none]'),
            ancestor_context=' > '.join(
                _safe_text(item) for item in raw.get('ancestors', []) if _safe_text(item)
            ) or '[none]',
            nearby_labels=tuple(
                _safe_text(item) for item in raw.get('nearby', []) if _safe_text(item)
            ),
        ))
    return ContinueDiagnostic(tuple(evidence))


def select_continue_control(page) -> ContinueControl:
    """Require the exact observed editor navigation control and reject lookalikes."""
    candidates = _visible_enabled(
        page.get_by_role('button', name='Continue', exact=True).all()
    )
    if len(candidates) != 1:
        raise BrowserSessionError(
            'The editor-scoped Continue control was missing or ambiguous; it was not clicked.'
        )
    control = candidates[0]
    try:
        evidence = control.evaluate(
            """node => ({
                tag: node.tagName.toLowerCase(),
                type: (node.getAttribute('type') || '').toLowerCase(),
                testId: node.getAttribute('data-testid') || '',
                inDialog: !!node.closest('[role="dialog"]'),
                inEditor: !!node.closest('.editor.newsletter-post-editor'),
                inForm: !!(node.form || node.closest('form')),
                formAction: node.getAttribute('formaction') || '',
                href: node.getAttribute('href') || '',
                text: (node.innerText || node.getAttribute('aria-label') || '').trim()
            })"""
        )
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError(
            'Continue control semantics could not be verified; it was not clicked.'
        ) from None
    exact_text = evidence.get('text') == 'Continue'
    navigation_semantics = (
        evidence.get('tag') == 'button'
        and evidence.get('type') == 'button'
        and evidence.get('testId') == 'publish-button'
        and not evidence.get('formAction')
        and not evidence.get('href')
        and not evidence.get('inForm')
        and bool(evidence.get('inEditor'))
    )
    if evidence.get('inDialog') or not exact_text or not navigation_semantics:
        raise BrowserSessionError(
            'Continue did not have unambiguous editor-navigation semantics; it was not clicked.'
        )
    return ContinueControl(
        control, 'button', 'Continue', 'publish-button',
        'button', 'div.editor.newsletter-post-editor',
    )


def close_preflight_dialogs(page, monitor) -> None:
    """Dismiss read-only Social Preview/settings dialogs with Escape, never Save/Done."""
    for selector in (
        '[role="dialog"][data-testid="modal"]',
        '[role="dialog"][data-testid="settings-modal"]',
    ):
        visible = [item for item in page.locator(selector).all() if item.is_visible()]
        if len(visible) > 1:
            raise BrowserSessionError('Preflight dialogs were ambiguous; Continue was not clicked.')
        if not visible:
            continue
        page.keyboard.press('Escape')
        deadline = monotonic() + 3
        while monotonic() < deadline:
            monitor.require_clear(page)
            if not any(item.is_visible() for item in page.locator(selector).all()):
                break
            page.wait_for_timeout(50)
        else:
            raise BrowserSessionError(
                'A read-only preflight dialog could not be safely dismissed; Continue was not clicked.'
            )


def _safe_text(value: object, fallback: str = '') -> str:
    text = ' '.join(str(value or '').split())[:240]
    if not text:
        return fallback
    if SENSITIVE.search(text):
        return '[sensitive value redacted]'
    return text


def _raw_controls(scope) -> list[dict]:
    """Collect visible semantic controls and labels from the bounded final-screen scope."""
    return scope.evaluate(
        """root => {
            const visible = node => {
                const s = getComputedStyle(node);
                const r = node.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' &&
                    (r.width > 0 || r.height > 0);
            };
            const roleFor = node => node.getAttribute('role') || ({
                BUTTON: 'button', A: 'link', INPUT: 'input', SELECT: 'combobox',
                TEXTAREA: 'textbox', LABEL: 'label', LEGEND: 'group-label',
                H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading'
            }[node.tagName] || 'text');
            const labelFor = node => {
                if (node.getAttribute('aria-label')) return node.getAttribute('aria-label');
                if (node.labels?.length) return [...node.labels].map(x => x.innerText).join(' ');
                if (node.getAttribute('aria-labelledby')) {
                    return node.getAttribute('aria-labelledby').split(/\\s+/)
                        .map(id => document.getElementById(id)?.innerText || '').join(' ');
                }
                return node.innerText || node.getAttribute('placeholder') || node.name || '';
            };
            const selector = 'button, a[href], input, select, textarea, label, legend, '
                + 'h1, h2, h3, h4, [role="button"], [role="radio"], '
                + '[role="checkbox"], [role="switch"], [role="combobox"]';
            return [...root.querySelectorAll(selector)].filter(visible).map(node => {
                const tag = node.tagName.toLowerCase();
                const type = (node.getAttribute('type') || tag).toLowerCase();
                let value = '';
                if (type === 'checkbox' || type === 'radio' ||
                        node.getAttribute('role') === 'checkbox' ||
                        node.getAttribute('role') === 'radio' ||
                        node.getAttribute('role') === 'switch') {
                    const checked = node.checked ?? node.getAttribute('aria-checked') === 'true';
                    value = checked ? 'Selected' : 'Not selected';
                } else if (tag === 'select') {
                    value = node.selectedOptions?.[0]?.text || '';
                } else if (tag === 'input' || tag === 'textarea') {
                    value = ['password', 'hidden', 'file'].includes(type) ? '' : node.value;
                }
                return {
                    label: labelFor(node), role: roleFor(node), type, value,
                    required: !!node.required || node.getAttribute('aria-required') === 'true',
                    disabled: !!node.disabled || node.getAttribute('aria-disabled') === 'true'
                };
            });
        }"""
    )


def _publication_controls(scope) -> tuple[PublicationControl, ...]:
    by_label = {}
    for raw in _raw_controls(scope):
        label = _safe_text(raw.get('label'))
        if not label:
            continue
        role = _safe_text(raw.get('role'), 'unknown')
        final = role in {'button', 'link'} and bool(FINAL_ACTION.fullmatch(label))
        required = bool(raw.get('required'))
        candidate = PublicationControl(
            label=label,
            role=role,
            control_type=_safe_text(raw.get('type'), 'unknown'),
            value=_safe_text(raw.get('value'), 'None'),
            required=required,
            optional=not required and not final,
            final_action=final,
        )
        existing = by_label.get(label)
        static_roles = {'label', 'group-label', 'heading', 'text'}
        if existing is None or (existing.role in static_roles and candidate.role not in static_roles):
            by_label[label] = candidate
    return tuple(by_label.values())


def _final_scope(page):
    dialogs = []
    for dialog in page.locator('[role="dialog"]').all():
        try:
            if dialog.is_visible():
                dialogs.append(dialog)
        except PlaywrightError:
            continue
    if len(dialogs) > 1:
        matching = [dialog for dialog in dialogs if any(
            FINAL_ACTION.fullmatch(_safe_text(item.get('label')))
            for item in _raw_controls(dialog)
        )]
        if len(matching) != 1:
            raise BrowserSessionError('The final publication screen was ambiguous.')
        return matching[0], 'dialog'
    if len(dialogs) == 1:
        return dialogs[0], 'dialog'
    return page.locator('body'), 'page'


def _safe_url(page, publication_url: str) -> str:
    if not trusted_page(page.url, publication_url):
        return '[unrecognized URL redacted]'
    parsed = urlsplit(page.url)
    return f'https://{parsed.hostname}{parsed.path.rstrip("/")}'


def inspect_final_publication_screen(
    page, publication_url: str, draft_url: str, monitor, guard: MutationGuard,
    *, continue_control: ContinueControl | None = None,
) -> FinalScreenInspection:
    """Click verified Continue once, collect the resulting UI, then exit safely if possible."""
    control = continue_control or select_continue_control(page)
    control.locator.click()
    deadline = monotonic() + 10
    controls = ()
    scope_kind = 'page'
    scope = None
    while monotonic() < deadline:
        monitor.require_clear(page)
        try:
            scope, scope_kind = _final_scope(page)
            controls = _publication_controls(scope)
        except (PlaywrightError, AttributeError, TypeError):
            controls = ()
        if any(item.final_action for item in controls):
            break
        page.wait_for_timeout(100)
    else:
        raise BrowserSessionError(
            'Continue was clicked once, but the final publication configuration screen '
            'could not be positively identified. No final action was clicked.'
        )
    final_actions = tuple(item.label for item in controls if item.final_action)
    if not final_actions:
        raise BrowserSessionError('No final publication action was identified.')
    heading = next((item.label for item in controls if item.role == 'heading'), '')
    try:
        browser_title = page.title()
    except PlaywrightError:
        browser_title = ''
    title = heading or _safe_text(browser_title, 'Final publication configuration')

    final_url = _safe_url(page, publication_url)
    exit_candidates = [
        item for item in controls
        if SAFE_EXIT.fullmatch(item.label) and item.role in {'button', 'link'}
    ]
    safe_exit = exit_candidates[0].label if len(exit_candidates) == 1 else None
    exit_behavior = 'Browser closed on final publication screen; no exit control was used.'
    if safe_exit is not None and scope is not None:
        candidates = _visible_enabled(
            scope.get_by_role(exit_candidates[0].role, name=safe_exit, exact=True).all()
        )
        if len(candidates) == 1:
            candidates[0].click()
            page.wait_for_timeout(250)
            monitor.require_clear(page)
            if page.url.rstrip('/') == draft_url.rstrip('/'):
                exit_behavior = f'{safe_exit} clicked once; returned to the draft editor.'
            else:
                exit_behavior = f'{safe_exit} clicked once; browser then closed without further action.'
        else:
            safe_exit = None
    return FinalScreenInspection(
        final_url, title, controls, final_actions,
        safe_exit, exit_behavior, tuple(guard.blocked_methods),
    )
