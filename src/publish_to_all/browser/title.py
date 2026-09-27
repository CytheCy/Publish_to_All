"""Conservative empty-title repair for one already linked draft editor."""

from dataclasses import dataclass
from time import monotonic

from playwright.sync_api import Error as PlaywrightError

from . import body, editor
from ..errors import BrowserSessionError


@dataclass(frozen=True)
class TitleInspection:
    title: str | None
    body_classification: body.BodyClassification


def inspect_empty_title_and_body(page) -> tuple[object, TitleInspection]:
    """Read exactly one editable title and body surface without changing either."""
    try:
        field = editor.unique_visible(editor.title_fields(page))
        if field is None or not field.is_editable():
            return field, TitleInspection(None, body.BodyClassification.UNKNOWN)
        title = editor.title_value(field)
        surface = body.locate_body_surface(page)
        body_inspection = body.inspect_body(surface, body.PreparedBody('', ''))
    except (BrowserSessionError, PlaywrightError, AttributeError, TypeError):
        return None, TitleInspection(None, body.BodyClassification.UNKNOWN)
    return field, TitleInspection(title, body_inspection.classification)


def insert_exact_title(field, title: str) -> None:
    """Fill an empty editable title field once, preserving the supplied text exactly."""
    try:
        if editor.title_value(field).strip():
            raise BrowserSessionError('The draft title changed before repair.')
        field.fill(title)
        if editor.title_value(field) != title:
            raise BrowserSessionError('The editor did not retain the exact repaired title.')
        field.blur()
    except BrowserSessionError:
        raise
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError('The editor did not accept the repaired title safely.') from None


def _saving_visible(page) -> bool:
    return any(item.is_visible() for item in page.get_by_text(editor.SAVING).all())


def confirm_title_save(
    page, field, title: str, publication_url: str, monitor: body.RateLimitMonitor,
    *, previously_saved: bool, timeout: float = 30,
) -> None:
    """Require the exact title and a fresh autosave signal on the same editor page."""
    deadline = monotonic() + timeout
    saw_unsaved = not previously_saved
    while True:
        monitor.require_clear(page)
        editor.require_publication(page, publication_url)
        try:
            current = editor.title_value(field)
            saved = editor.save_visible(page)
            saving = _saving_visible(page)
        except (PlaywrightError, AttributeError, TypeError):
            raise BrowserSessionError('The repaired title or save state could not be verified.') from None
        if current != title:
            raise BrowserSessionError('The visible repaired title no longer matches exactly.')
        saw_unsaved = saw_unsaved or saving or not saved
        if saved and not saving and saw_unsaved:
            return
        if monotonic() >= deadline:
            raise BrowserSessionError('Could not confirm a fresh Substack autosave after title repair.')
        page.wait_for_timeout(250)
