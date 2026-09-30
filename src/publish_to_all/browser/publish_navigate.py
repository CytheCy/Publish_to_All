"""Nonfinal navigation around the read-only Substack publication inspector."""

from dataclasses import replace
from time import monotonic

from playwright.sync_api import Error as PlaywrightError

from ..errors import BrowserSessionError
from .publish_inspect import (
    ContinueControl, MutationGuard, _final_scope, _visible_enabled,
    inspect_open_final_publication_screen, select_continue_control,
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


def open_and_inspect_final_publication_screen(
    page, publication_url: str, draft_url: str, monitor, guard: MutationGuard,
    *, continue_control: ContinueControl | None = None,
    leave_open: bool = False,
):
    """Navigate with Continue, then delegate all DOM reading to the inspector."""
    control = continue_control or select_continue_control(page)
    control.locator.click()
    guard.require_clear()
    deadline = monotonic() + 10
    screen = None
    while monotonic() < deadline:
        monitor.require_clear(page)
        guard.require_clear()
        try:
            screen = inspect_open_final_publication_screen(page, publication_url)
        except (BrowserSessionError, PlaywrightError, AttributeError, TypeError, ValueError):
            screen = None
        if screen is not None:
            break
        page.wait_for_timeout(100)
    if screen is None:
        raise BrowserSessionError(
            'Continue was clicked once, but the final publication configuration screen '
            'could not be positively identified. No final action was clicked.'
        )

    if leave_open:
        return replace(
            screen, safe_exit=None,
            exit_behavior='Browser closed on final publication screen; no exit control was used.',
            blocked_mutation_methods=tuple(guard.blocked_methods),
        )

    exit_candidates = [item for item in screen.controls if item.safe_navigation]
    safe_exit = exit_candidates[0].label if len(exit_candidates) == 1 else None
    exit_behavior = 'Browser closed on final publication screen; no exit control was used.'
    if safe_exit is not None:
        scope, _scope_kind = _final_scope(page)
        candidates = _visible_enabled(
            scope.get_by_role(exit_candidates[0].role, name=safe_exit, exact=True).all()
        )
        if len(candidates) == 1:
            candidates[0].click()
            page.wait_for_timeout(250)
            monitor.require_clear(page)
            guard.require_clear()
            if page.url.rstrip('/') == draft_url.rstrip('/'):
                exit_behavior = f'{safe_exit} clicked once; returned to the draft editor.'
            else:
                exit_behavior = f'{safe_exit} clicked once; browser then closed without further action.'
        else:
            safe_exit = None
    return replace(
        screen, safe_exit=safe_exit, exit_behavior=exit_behavior,
        blocked_mutation_methods=tuple(guard.blocked_methods),
    )
