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


def open_new_post(page: Page, publication_url: str) -> None:
    require_publication(page, publication_url)
    # Never initiate creation if navigation has already reached an editor.
    if extract_draft_url(page, publication_url) or urlsplit(page.url).path.rstrip('/') == '/publish/post/new':
        raise BrowserSessionError('An existing editor is open; reconcile before retrying.')
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
    create = wait_visible(page, controls(page, CREATE))
    click_creation_control(page, create, publication_url, CREATE)
    # Some versions open an article directly; others expose Create > Article.
    choice = wait_visible(page, title_fields(page).or_(controls(page, ARTICLE)))
    if unique_visible(title_fields(page)) is None:
        click_creation_control(page, choice, publication_url, ARTICLE)


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
    if title_value(field):
        raise BrowserSessionError("Expected an empty new-post title; existing content was left untouched.")
    field.fill(title)
    if title_value(field) != title:
        raise BrowserSessionError("The editor did not retain the exact title.")
    field.blur()


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


def confirm_draft(page, field, title, publication_url, *, previously_saved=False, timeout=30):
    """Require fresh saved UI evidence or the exact title in a separately loaded draft.

    A URL alone or a stale Saved label never establishes title persistence. Keep
    the editing tab open while a read-only verification tab checks the known URL.
    """
    deadline = monotonic() + timeout
    saw_unsaved = not previously_saved
    verification = None
    next_read = 0.0
    try:
        while True:
            require_publication(page, publication_url)
            saved = save_visible(page)
            saving = any(item.is_visible() for item in page.get_by_text(SAVING).all())
            saw_unsaved = saw_unsaved or not saved or saving
            if saved and not saving and saw_unsaved and title_value(field) == title:
                return extract_draft_url(page, publication_url)
            url = extract_draft_url(page, publication_url)
            now = monotonic()
            if url and now >= next_read:
                if verification is None:
                    verification = page.context.new_page()
                verification.goto(url, wait_until='domcontentloaded')
                require_publication(verification, publication_url)
                other = unique_visible(title_fields(verification))
                if other is not None and title_value(other) == title:
                    return url
                next_read = monotonic() + 1
            if monotonic() >= deadline:
                raise BrowserSessionError("Could not confirm that the title was saved as a draft.")
            page.wait_for_timeout(250)
    finally:
        if verification is not None:
            verification.close()
