"""Conservative subtitle editing for one already linked Substack draft."""

from time import monotonic
import re

from playwright.sync_api import Error as PlaywrightError

from . import editor
from ..errors import BrowserSessionError


# This is semantic editor evidence: the accessible name/placeholder is the
# field identity, with data-testid as an additional stable assertion. No DOM
# position or CSS order is used.
SUBTITLE_NAME = re.compile(r"^(Subtitle|Add a subtitle)(?:…|\.\.\.)?$", re.I)
SUBTITLE_TEST_ID = '[data-testid="subtitle"]'


def subtitle_fields(page):
    return (
        page.get_by_role('textbox', name=SUBTITLE_NAME)
        .or_(page.get_by_placeholder(SUBTITLE_NAME))
        .or_(page.locator(SUBTITLE_TEST_ID))
    )


def subtitle_value(field) -> str:
    if field.get_attribute('contenteditable') == 'true':
        return field.inner_text()
    return field.input_value()


def locate_subtitle_field(page):
    try:
        field = editor.unique_visible(subtitle_fields(page))
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError('The Substack subtitle field could not be inspected safely.') from None
    if field is None:
        raise BrowserSessionError(
            'The Substack subtitle field was not identified by stable semantic editor evidence.'
        )
    if not field.is_editable():
        raise BrowserSessionError('The Substack subtitle field is not editable.')
    return field


def inspect_subtitle(page) -> tuple[object | None, str | None]:
    """Read the one semantic subtitle control without changing the page."""
    try:
        field = editor.unique_visible(subtitle_fields(page))
        if field is None or not field.is_editable():
            return None, None
        return field, subtitle_value(field)
    except (PlaywrightError, AttributeError, TypeError):
        return None, None


def insert_exact_subtitle(field, description: str) -> None:
    """Fill an empty subtitle exactly once; never replace existing content."""
    try:
        current = subtitle_value(field)
        if current.strip():
            raise BrowserSessionError('The draft subtitle already contains different content.')
        field.fill(description)
        if subtitle_value(field) != description:
            raise BrowserSessionError('The editor did not retain the exact subtitle.')
        field.blur()
    except BrowserSessionError:
        raise
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError('The editor did not accept the subtitle safely.') from None


def confirm_subtitle_save(
    page, field, description: str, publication_url: str, monitor,
    *, expected_url: str, title: str, body_surface, body_text: str,
    previously_saved: bool, timeout: float = 30,
) -> None:
    """Verify subtitle, title, body, URL, and a fresh Saving -> Saved transition."""
    deadline = monotonic() + timeout
    while True:
        monitor.require_clear(page)
        editor.require_publication(page, publication_url)
        if editor.extract_draft_url(page, publication_url) != expected_url:
            raise BrowserSessionError('The editor left the linked numeric draft URL.')
        current = editor.unique_visible(subtitle_fields(page))
        if current is None or not current.is_editable() or subtitle_value(current) != description:
            raise BrowserSessionError('The visible subtitle no longer matches exactly.')
        title_field = editor.unique_visible(editor.title_fields(page))
        if title_field is None or editor.title_value(title_field).strip() != title:
            raise BrowserSessionError('The draft title changed during subtitle preparation.')
        if ' '.join(body_surface.inner_text().split()) != body_text:
            raise BrowserSessionError('The draft body changed during subtitle preparation.')
        saved = editor.save_visible(page)
        saving = editor.saving_visible(page)
        saw_saving, saw_saved_after_saving = editor.observed_save_transition(page)
        if saw_saving and saw_saved_after_saving and saved and not saving:
            return
        if monotonic() >= deadline:
            raise BrowserSessionError('Could not confirm a fresh Saving to Saved transition for the subtitle.')
        page.wait_for_timeout(250)
