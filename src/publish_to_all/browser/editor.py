"""Conservative title-only editor actions. No private API or publication actions."""

import re
from time import monotonic
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Page

from ..errors import BrowserSessionError
from .substack import AuthenticationState, detect_authentication, trusted_page


DANGEROUS = re.compile(r"\b(publish(?:ing)?|send|schedule|continue)\b", re.I)
CREATE = re.compile(r"^(Create|New post|Create post|Write|Write a post|New article)$", re.I)
ARTICLE = re.compile(r"^(Article|Text post|New text post|Post)$", re.I)
TITLE = re.compile(r"^(Title|Post title|Add a title)$", re.I)
SAVED = re.compile(r"^(Saved|Draft saved|All changes saved|Saved to drafts)$", re.I)
SAVING = re.compile(r"^Saving(?: changes)?(?:\.{3}|…)?$", re.I)

_SAVE_OBSERVER = "__publishToAllTitleSaveObservation"


def is_publishing_control(label: str) -> bool:
    return bool(DANGEROUS.search(label))


def require_publication(page: Page, publication_url: str) -> None:
    if (not trusted_page(page.url, publication_url)
            or urlsplit(page.url).hostname != urlsplit(publication_url).hostname
            or not urlsplit(page.url).path.startswith('/publish/')):
        raise BrowserSessionError("Cannot confirm the configured publication's editor/dashboard.")


def navigate_dashboard(page: Page, publication_url: str) -> None:
    # Draft preflight may already have established this exact dashboard page.
    # Reuse it to avoid a duplicate Substack request within the same command.
    try:
        require_publication(page, publication_url)
        if detect_authentication(page, publication_url) == AuthenticationState.AUTHENTICATED:
            return
    except BrowserSessionError:
        pass
    page.goto(publication_url + '/publish/home', wait_until='domcontentloaded')
    deadline = monotonic() + 10
    while True:
        require_publication(page, publication_url)
        state = detect_authentication(page, publication_url)
        if state == AuthenticationState.AUTHENTICATED:
            return
        if state == AuthenticationState.NOT_AUTHENTICATED or monotonic() >= deadline:
            raise BrowserSessionError("Cannot confirm authenticated dashboard access.")
        page.wait_for_timeout(200)


def controls(page: Page, names):
    return (page.get_by_role('button', name=names)
            .or_(page.get_by_role('link', name=names))
            .or_(page.get_by_role('menuitem', name=names)))


def unique_visible(locator):
    visible = [item for item in locator.all() if item.is_visible()]
    if len(visible) > 1:
        raise BrowserSessionError("Ambiguous editor controls; stopped without guessing.")
    return visible[0] if visible else None


def wait_visible(page, locator, timeout=10):
    deadline = monotonic() + timeout
    while True:
        found = unique_visible(locator)
        if found is not None:
            return found
        if monotonic() >= deadline:
            raise BrowserSessionError("Expected editor control is unavailable.")
        page.wait_for_timeout(200)


def click_creation_control(page, locator, publication_url, allowed):
    require_publication(page, publication_url)
    # Check both visible and accessible text; allow only specific creation actions.
    labels = [locator.inner_text().strip(), locator.get_attribute('aria-label') or '']
    labels = [label for label in labels if label]
    if (not labels or any(is_publishing_control(label) for label in labels)
            or not all(allowed.fullmatch(label) for label in labels)):
        raise BrowserSessionError("Refused an unrecognized or publishing control.")
    href = locator.get_attribute('href')
    if href:
        target = urljoin(page.url, href)
        if (not trusted_page(target, publication_url)
                or urlsplit(target).hostname != urlsplit(publication_url).hostname):
            raise BrowserSessionError("Creation link points outside the configured publication.")
        if urlsplit(target).path.rstrip('/') not in {'/publish/post', '/publish/post/new'}:
            raise BrowserSessionError("Creation link does not identify a new-post editor.")
    locator.click()


def title_fields(page):
    return page.get_by_role('textbox', name=TITLE).or_(page.get_by_placeholder(TITLE))


def open_new_post(page: Page, publication_url: str, on_draft_url=None) -> None:
    require_publication(page, publication_url)
    # Never initiate creation if navigation has already reached an editor.
    if extract_draft_url(page, publication_url) or urlsplit(page.url).path.rstrip('/') == '/publish/post/new':
        raise BrowserSessionError('An existing editor is open; reconcile before retrying.')
    observed = set()

    def capture(_frame=None):
        url = extract_draft_url(page, publication_url)
        if url and url not in observed:
            observed.add(url)
            if on_draft_url is not None:
                on_draft_url(url)

    # A numeric editor URL can appear before the React editor and title input
    # hydrate. Listen before the creation click so identity is persisted at the
    # earliest navigation event, including a click that later raises.
    listener_installed = hasattr(page, 'on') and hasattr(page, 'remove_listener')
    if listener_installed:
        page.on('framenavigated', capture)
    candidates = controls(page, CREATE)
    # A rendered Posts link is a conservative fallback to the creator listing.
    if unique_visible(candidates) is None:
        posts = unique_visible(controls(page, re.compile(r'^Posts$', re.I)))
        if posts is not None:
            href = posts.get_attribute('href')
            target = urljoin(page.url, href) if href else ''
            if target.rstrip('/') == publication_url + '/publish/posts':
                page.goto(target, wait_until='domcontentloaded')
                require_publication(page, publication_url)
    try:
        create = wait_visible(page, controls(page, CREATE))
        click_creation_control(page, create, publication_url, CREATE)
        capture()
        # Some versions open an article directly; others expose Create > Article.
        choice = wait_visible(page, title_fields(page).or_(controls(page, ARTICLE)))
        capture()
        if unique_visible(title_fields(page)) is None:
            click_creation_control(page, choice, publication_url, ARTICLE)
            capture()
    finally:
        if listener_installed:
            page.remove_listener('framenavigated', capture)


def locate_title_field(page: Page, publication_url: str):
    field = wait_visible(page, title_fields(page))
    require_publication(page, publication_url)
    if not field.is_editable():
        raise BrowserSessionError("Title field is not editable.")
    return field


def title_value(field) -> str:
    if field.get_attribute('contenteditable') == 'true':
        return field.inner_text()
    return field.input_value()


def enter_title(page, field, title: str, publication_url: str) -> None:
    require_publication(page, publication_url)
    if not field.is_visible() or not field.is_editable() or title_value(field):
        raise BrowserSessionError("Expected an empty new-post title; existing content was left untouched.")
    field.fill(title)
    current = unique_visible(title_fields(page))
    if current is None or not current.is_editable() or title_value(current) != title:
        raise BrowserSessionError("The editor did not retain the exact title.")
    current.blur()
    return current


def extract_draft_url(page, publication_url: str) -> str | None:
    """Recognize only observed editor URLs; never build or request guessed routes."""
    if not trusted_page(page.url, publication_url):
        return None
    parsed = urlsplit(page.url)
    if (parsed.hostname != urlsplit(publication_url).hostname
            or not re.fullmatch(r'/publish/post/[1-9][0-9]*/?', parsed.path)):
        return None
    # Strip tracking queries/fragments rather than persisting possible secrets.
    return f'https://{parsed.hostname}{parsed.path}'


def save_visible(page) -> bool:
    return any(item.is_visible() for item in page.get_by_text(SAVED).all())


def saving_visible(page) -> bool:
    return any(item.is_visible() for item in page.get_by_text(SAVING).all())


def start_save_observation(page) -> None:
    """Observe a future Saving -> Saved transition without trusting existing text."""
    if saving_visible(page):
        raise BrowserSessionError('The editor was already saving before the title edit.')
    page.evaluate(
        """key => {
            const previous = window[key];
            if (previous && previous.observer) previous.observer.disconnect();
            const state = {sawSaving: false, sawSavedAfterSaving: false, observer: null};
            const saving = /^Saving(?: changes)?(?:\\.{3}|…)?$/i;
            const saved = /^(Saved|Draft saved|All changes saved|Saved to drafts)$/i;
            const visibleTexts = () =>
                Array.from(document.querySelectorAll('body *'))
                    .filter(node => node.children.length === 0 && node.getClientRects().length > 0)
                    .map(node => (node.textContent || '').trim());
            const record = text => {
                text = (text || '').trim();
                if (saving.test(text)) state.sawSaving = true;
            };
            const scan = mutations => {
                for (const mutation of mutations || []) {
                    record(mutation.oldValue);
                    for (const node of [...mutation.addedNodes, ...mutation.removedNodes]) {
                        record(node.textContent);
                    }
                }
                const texts = visibleTexts();
                const hasSaving = texts.some(text => saving.test(text));
                const hasSaved = texts.some(text => saved.test(text));
                if (hasSaving) state.sawSaving = true;
                if (state.sawSaving && hasSaved && !hasSaving) state.sawSavedAfterSaving = true;
            };
            state.observer = new MutationObserver(scan);
            state.observer.observe(document.documentElement, {
                subtree: true, childList: true, characterData: true,
                characterDataOldValue: true
            });
            window[key] = state;
        }""",
        _SAVE_OBSERVER,
    )


def observed_save_transition(page) -> tuple[bool, bool]:
    result = page.evaluate(
        """key => {
            const state = window[key];
            return state ? [state.sawSaving, state.sawSavedAfterSaving] : [false, false];
        }""",
        _SAVE_OBSERVER,
    )
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        return False, False
    return result[0] is True, result[1] is True


def confirm_draft(
    page, field, title, publication_url, *, expected_draft_url: str | None = None,
    previously_saved=False, timeout=30,
):
    """Require the exact title and a fresh same-page Saving -> Saved transition."""
    deadline = monotonic() + timeout
    expected = (expected_draft_url or extract_draft_url(page, publication_url))
    if expected is None:
        raise BrowserSessionError('The captured numeric draft URL is unavailable.')
    expected = expected.rstrip('/')
    while True:
        require_publication(page, publication_url)
        current_url = extract_draft_url(page, publication_url)
        if current_url is None or current_url.rstrip('/') != expected:
            raise BrowserSessionError('The editor left the captured numeric draft URL.')
        current = unique_visible(title_fields(page))
        if current is None or not current.is_editable() or title_value(current) != title:
            raise BrowserSessionError('The visible title no longer matches exactly.')
        saw_saving, saw_saved_after_saving = observed_save_transition(page)
        saved = save_visible(page)
        saving = saving_visible(page)
        if saw_saving and saw_saved_after_saving and saved and not saving:
            return expected
        if monotonic() >= deadline:
            raise BrowserSessionError(
                'Could not confirm a fresh Saving to Saved transition for the title edit.'
            )
        page.wait_for_timeout(100)
