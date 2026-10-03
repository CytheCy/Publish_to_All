"""Conservative rendered-editor body insertion for an already linked draft."""

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from playwright.sync_api import Error as PlaywrightError

from . import editor
from .substack import rate_limit_evidence, trusted_page
from ..errors import BrowserSessionError, SubstackRateLimitError


class BodyClassification(StrEnum):
    EMPTY = 'empty'
    MINIMAL = 'minimal'
    SUBSTANTIAL = 'substantial'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class PreparedBody:
    html: str
    text: str


@dataclass(frozen=True)
class BodyInspection:
    classification: BodyClassification
    visible_text: str | None
    already_contains_story: bool = False


def normalize_visible_text(value: str) -> str:
    return ' '.join(value.split())


def _inline_text(token) -> str:
    return ''.join(
        child.content if child.type in {'text', 'code_inline'} else
        '\n' if child.type in {'softbreak', 'hardbreak'} else
        child.content if child.type == 'image' else ''
        for child in token.children or ()
    )


def prepare_story_body(story) -> PreparedBody:
    """Use the parsed body and rendered CommonMark HTML; front matter is absent."""
    blocks = []
    for token in story.tokens:
        if token.type == 'inline':
            blocks.append(_inline_text(token))
        elif token.type in {'fence', 'code_block'}:
            blocks.append(token.content)
    return PreparedBody(story.html, '\n\n'.join(blocks))


def body_surfaces(page):
    primary = page.locator('.ProseMirror, [data-lexical-editor="true"]')
    candidates = [item for item in primary.all() if item.is_visible() and item.is_editable()]
    if candidates:
        return candidates
    fallback = page.locator('[contenteditable="true"][role="textbox"]')
    return [
        item for item in fallback.all()
        if item.is_visible() and item.is_editable()
        and not any(
            (item.get_attribute(attribute) or '').strip().lower()
            in {'title', 'post title', 'add a title'}
            for attribute in ('aria-label', 'placeholder', 'data-placeholder')
        )
    ]


def locate_body_surface(page):
    try:
        surfaces = body_surfaces(page)
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError('The editable body surface could not be identified safely.') from None
    if len(surfaces) != 1:
        raise BrowserSessionError('The draft does not expose exactly one identifiable editable body surface.')
    return surfaces[0]


def classify_body_text(text: str | None) -> BodyClassification:
    if text is None:
        return BodyClassification.UNKNOWN
    length = len(normalize_visible_text(text))
    if length == 0:
        return BodyClassification.EMPTY
    if length <= 100:
        return BodyClassification.MINIMAL
    return BodyClassification.SUBSTANTIAL


def contains_prepared_body(remote_text: str, prepared: PreparedBody) -> bool:
    remote = normalize_visible_text(remote_text)
    local = normalize_visible_text(prepared.text)
    return bool(local and (remote == local or (len(local) > 500 and local in remote)))


def inspect_body(surface, prepared: PreparedBody) -> BodyInspection:
    try:
        text = surface.inner_text()
    except (PlaywrightError, AttributeError, TypeError):
        return BodyInspection(BodyClassification.UNKNOWN, None)
    return BodyInspection(
        classify_body_text(text), text, contains_prepared_body(text, prepared),
    )


class RateLimitMonitor:
    """Capture an autosave HTTP 429 without performing another request."""

    def __init__(self, publication_url: str):
        self.publication_url = publication_url
        self.encountered = False

    def observe(self, response) -> None:
        try:
            if response.status == 429 and trusted_page(response.url, self.publication_url):
                self.encountered = True
        except (AttributeError, TypeError, ValueError):
            pass

    def require_clear(self, page) -> None:
        if self.encountered or rate_limit_evidence(page, self.publication_url):
            raise SubstackRateLimitError('Substack rate limit encountered during body insertion or save confirmation.')


_INSERT_HTML = r"""
(element, content) => {
  if (!element.isContentEditable) throw new Error('body is not contenteditable');
  if (element.innerText.trim().length !== 0) throw new Error('body changed before insertion');
  // CommonMark serializes block boundaries with newlines. In an editor using
  // pre-wrap/break-spaces, Chromium insertHTML turns those text nodes into
  // visible paragraphs. Remove only serialization whitespace in block-only
  // containers; retain inline spaces, BRs, explicit empty blocks and code.
  const fragment = document.createElement('div');
  fragment.innerHTML = content.html;
  const block = 'p,h1,h2,h3,h4,h5,h6,blockquote,ul,ol,li,pre,hr';
  for (const parent of [fragment, ...fragment.querySelectorAll('blockquote,ul,ol,li')]) {
    if (parent.closest('pre,code')) continue;
    for (const node of [...parent.childNodes]) {
      const boundary = n => !n || (n.nodeType === 1 && n.matches(block));
      if (node.nodeType === 3 && /^[\t\r\n ]+$/.test(node.nodeValue)
          && node.nodeValue.includes('\n')
          && boundary(node.previousSibling) && boundary(node.nextSibling)) node.remove();
    }
  }
  element.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(element);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  const inserted = document.execCommand('insertHTML', false, fragment.innerHTML);
  if (!inserted) throw new Error('formatted insertion was rejected');
  element.dispatchEvent(new InputEvent('input', {
    bubbles: true, inputType: 'insertFromPaste', data: content.text
  }));
}
"""


def insert_prepared_body(surface, prepared: PreparedBody) -> None:
    """Insert formatted HTML once through the focused rendered editor."""
    try:
        surface.evaluate(_INSERT_HTML, {'html': prepared.html, 'text': prepared.text})
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError('The rendered editor did not accept the formatted story body.') from None


def _saving_visible(page) -> bool:
    return any(item.is_visible() for item in page.get_by_text(editor.SAVING).all())


def confirm_body_save(
    page, surface, prepared: PreparedBody, publication_url: str, monitor: RateLimitMonitor,
    *, previously_saved: bool, timeout: float = 30,
) -> None:
    """Require exact rendered body text and a fresh, bounded autosave signal."""
    deadline = monotonic() + timeout
    saw_unsaved = not previously_saved
    while True:
        monitor.require_clear(page)
        editor.require_publication(page, publication_url)
        current = inspect_body(surface, prepared)
        if current.visible_text is None or not current.already_contains_story:
            raise BrowserSessionError('The editor body could not be verified after insertion.')
        saved = editor.save_visible(page)
        saving = _saving_visible(page)
        saw_unsaved = saw_unsaved or saving or not saved
        if saved and not saving and saw_unsaved:
            return
        if monotonic() >= deadline:
            raise BrowserSessionError('Could not confirm a fresh Substack autosave after body insertion.')
        page.wait_for_timeout(250)
