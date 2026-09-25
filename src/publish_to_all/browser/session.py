"""Reusable Chromium lifecycle and private, best-effort failure diagnostics."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

from playwright.sync_api import BrowserContext, Error as PlaywrightError, sync_playwright

from ..errors import BrowserSessionError


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


# Preserve layout for diagnostics without storing text, avatars, form values,
# charts, embedded documents, or CSS images. Styles apply only during capture.
REDACTED_SCREENSHOT_STYLE = """
* { color: transparent !important; -webkit-text-fill-color: transparent !important;
    text-shadow: none !important; background-image: none !important;
    list-style-image: none !important; }
*::before, *::after { content: none !important; }
input, textarea, select, img, svg, canvas, video, iframe, object, embed {
    visibility: hidden !important;
}
"""


def safe_screenshot(page, diagnostics: Path) -> Path | None:
    target = None
    try:
        private_directory(diagnostics)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = diagnostics / f"substack-{stamp}-{uuid4().hex}.png"
        with target.open("xb"):
            target.chmod(0o600)
        page.screenshot(path=str(target), timeout=5000, animations="disabled",
                        style=REDACTED_SCREENSHOT_STYLE)
        return target
    except (PlaywrightError, OSError):
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        return None


def browser_failure(context: BrowserContext | None, diagnostics: Path) -> BrowserSessionError:
    # Never include Playwright's raw exception: URLs may contain login tokens.
    message = (
        "Substack browser operation failed. Check your connection, close other browsers "
        "using this profile, and ensure Chromium is installed "
        "(python -m playwright install chromium) and a graphical desktop is available."
    )
    screenshot = None
    if context is not None:
        try:
            pages = [page for page in context.pages if not page.is_closed()]
            if pages:
                screenshot = safe_screenshot(pages[-1], diagnostics)
        except (PlaywrightError, OSError):
            pass
    if screenshot:
        message += f" Screenshot: {screenshot}. Page text and images are redacted."
    else:
        message += " Screenshot unavailable (no usable page or capture failed)."
    return BrowserSessionError(message)


@contextmanager
def persistent_browser(profile: Path, diagnostics: Path, *, headless: bool = False):
    """Use a dedicated profile; closing the persistent context closes Chromium."""
    context = None
    try:
        private_directory(profile)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=headless, accept_downloads=False,
            )
            try:
                context.set_default_timeout(5000)
                context.set_default_navigation_timeout(30000)
                yield context
            except (PlaywrightError, OSError):
                raise browser_failure(context, diagnostics) from None
            finally:
                failing = sys.exc_info()[0] is not None
                try:
                    context.close()
                except PlaywrightError:
                    if not failing:
                        raise browser_failure(context, diagnostics) from None
    except (PlaywrightError, OSError):
        raise browser_failure(context, diagnostics) from None
