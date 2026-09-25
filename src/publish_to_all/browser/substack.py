"""Read-only Substack navigation and conservative rendered authentication checks."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from time import monotonic
from urllib.parse import urlsplit

from playwright.sync_api import Page, Error as PlaywrightError

from ..config import load_config, runtime_paths
from .session import persistent_browser, safe_screenshot


class AuthenticationState(str, Enum):
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionDiagnostics:
    final_url: str
    title: str
    controls: tuple[str, ...]
    screenshot: Path | None


@dataclass(frozen=True)
class SessionResult:
    publication_url: str
    profile: Path
    authentication: AuthenticationState
    diagnostics: SessionDiagnostics | None = None


def trusted_page(url: str, publication_url: str) -> bool:
    """Permit HTTPS publication/custom-domain and Substack redirects, not lookalikes."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return bool(
            parsed.scheme == "https" and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and (host == urlsplit(publication_url).hostname
                 or host == "substack.com" or host.endswith(".substack.com"))
        )
    except ValueError:
        return False


def _visible(scope, role: str, name: str) -> bool:
    locator = scope.get_by_role(role, name=re.compile(name, re.IGNORECASE))
    return any(item.is_visible() for item in locator.all())


# Match whole accessible names, never arbitrary account names or page text.
ACCOUNT = r"^(account|profile|user)( menu)?$|^open (account|profile|user) menu$|^avatar$"
DASHBOARD = r"^(dashboard|publisher dashboard|creator dashboard|publication dashboard|admin)$"
LOGIN = r"^(sign in|log in|login|sign in to substack|log in to substack)$"
AUTH_PATHS = {"sign-in", "signin", "login", "log-in", "auth", "account/login"}
SAFE_LABELS = (
    "New post", "Create", "Create post", "Write", "Write a post", "New article",
    "Article", "Text post", "New text post", "Post", "Drafts",
    "Home", "Dashboard", "Publisher dashboard", "Creator dashboard", "Publication dashboard",
    "Account", "Profile", "User", "Account menu", "Profile menu", "User menu", "Avatar",
    "Open account menu", "Open profile menu", "Open user menu", "Admin",
    "Posts", "Subscribers", "Stats", "Statistics", "Settings", "Audience",
    "Sign out", "Log out", "Logout", "Sign in", "Log in", "Login",
    "Sign in to Substack", "Log in to Substack", "Continue with email",
    "Enter your email", "Email", "Email address", "Password", "Subscribe",
    "Inbox", "Notifications", "Library", "Manage subscription", "My subscriptions",
)


@dataclass(frozen=True)
class Evidence:
    positive: bool = False
    negative: bool = False

    @property
    def state(self) -> AuthenticationState:
        if self.positive and not self.negative:
            return AuthenticationState.AUTHENTICATED
        if self.negative and not self.positive:
            return AuthenticationState.NOT_AUTHENTICATED
        return AuthenticationState.UNKNOWN


def authentication_evidence(page: Page, publication_url: str) -> Evidence:
    """Read rendered controls only. A dashboard URL alone never proves access."""
    if not trusted_page(page.url, publication_url):
        return Evidence()
    controls = page.get_by_role("navigation").or_(page.get_by_role("menu"))

    def has(pattern: str) -> bool:
        return any(_visible(page, role, pattern) for role in ("button", "link", "menuitem"))

    sign_out = has(r"^(sign out|log out|logout)$")
    account = has(ACCOUNT)
    dashboard = has(DASHBOARD) or _visible(controls, "link", DASHBOARD)
    publisher_navigation = all(
        _visible(controls, "link", label)
        for label in (r"^posts$", r"^subscribers$", r"^(stats|statistics)$")
    )
    reader_navigation = all(has(label) for label in (r"^inbox$", r"^(library|my subscriptions)$"))
    parsed = urlsplit(page.url)
    path = parsed.path.strip("/").lower()
    login_redirect = any(path == route or path.startswith(route + "/") for route in AUTH_PATHS)
    # The configured publication's creator shell plus two independent controls
    # establishes dashboard access, even when the account menu is icon-only.
    publication_dashboard = (
        parsed.hostname == urlsplit(publication_url).hostname
        and path in {"publish", "publish/home", "publish/posts"}
        and (_visible(page, "heading", DASHBOARD) or _visible(page, "link", r"^posts$"))
        and has(r"^(subscribers|audience)$")
        and has(r"^(stats|statistics|settings)$")
    )
    login_form = (_visible(page, "textbox", r"^(email|email address|enter your email)$")
                  and has(r"^(continue|continue with email|sign in|log in)$"))
    return Evidence(
        sign_out or (account and dashboard) or publisher_navigation
        or (account and reader_navigation) or publication_dashboard,
        login_redirect or login_form or has(LOGIN) or _visible(page, "heading", LOGIN),
    )


def detect_authentication(page: Page, publication_url: str) -> AuthenticationState:
    return authentication_evidence(page, publication_url).state


def _settle(page: Page, publication_url: str, timeout: float) -> Evidence:
    deadline = monotonic() + timeout
    # Poll through the entire settling window: early guest controls can be replaced
    # during hydration, and client-side redirects may follow DOMContentLoaded.
    while True:
        evidence = authentication_evidence(page, publication_url)
        if monotonic() >= deadline:
            return evidence
        page.wait_for_timeout(250)


def verify_page(page: Page, publication_url: str, *, timeout: float = 10) -> AuthenticationState:
    page.goto(publication_url, wait_until="domcontentloaded")
    first = _settle(page, publication_url, timeout)
    page.goto(publication_url + "/publish/home", wait_until="domcontentloaded")
    second = _settle(page, publication_url, timeout)
    # Preserve contradictions within a page as well as between the two visits.
    if any(e.positive and e.negative for e in (first, second)):
        return AuthenticationState.UNKNOWN
    states = {first.state, second.state} - {AuthenticationState.UNKNOWN}
    if len(states) == 1:
        return states.pop()
    return AuthenticationState.UNKNOWN


def collect_diagnostics(page: Page, publication_url: str, directory: Path) -> SessionDiagnostics:
    # Do not echo query strings, fragments, account identifiers, or arbitrary labels.
    url = "[unrecognized URL redacted]"
    title = "[nonstandard page title redacted]"
    labels = []
    try:
        if trusted_page(page.url, publication_url):
            parsed = urlsplit(page.url)
            path = parsed.path.rstrip("/")
            allowed = {"", "/home", "/publish", "/publish/home", "/publish/posts", "/account"}
            allowed.update("/" + route for route in AUTH_PATHS)
            url = f"https://{parsed.hostname}" + (path if path in allowed or re.fullmatch(r"/publish/post/[1-9][0-9]*", path) else "/[path redacted]")
        raw_title = page.title().strip()
        # Allow only fixed UI vocabulary; publication/account names are not logged.
        if isinstance(raw_title, str) and re.fullmatch(r"(?:Substack|Home|Dashboard|Sign in|Log in|Login|Publish|Posts)(?: [|–—-] (?:Substack|Home|Dashboard))*", raw_title, re.I):
            title = raw_title
        for label in SAFE_LABELS:
            if any(_visible(page, role, "^" + re.escape(label) + "$")
                   for role in ("button", "link", "menuitem", "heading", "textbox")):
                labels.append(label)
                if len(labels) == 16:
                    break
    except PlaywrightError:
        pass
    return SessionDiagnostics(url, title, tuple(labels), safe_screenshot(page, directory))


def inspect_substack_session(
    root: Path, *, complete_login: Callable[[], None] | None = None, debug: bool = False,
) -> SessionResult:
    """No input story or database is opened. The callback is terminal-only consent to check."""
    publication_url = load_config(root / "config.toml").require_substack()
    paths = runtime_paths(root)
    profile = paths.substack_browser_profile
    with persistent_browser(profile, paths.diagnostics) as context:
        # Do not reuse a restored tab: it could be a previously open editor.
        page = context.new_page()
        if complete_login is not None:
            page.goto(publication_url + "/publish/home", wait_until="domcontentloaded")
            complete_login()
            # Manual login can open additional tabs; verify in a new, known page.
            page = context.new_page()
        state = verify_page(page, publication_url)
        diagnostics = (collect_diagnostics(page, publication_url, paths.diagnostics)
                       if debug or state == AuthenticationState.UNKNOWN else None)
    return SessionResult(publication_url, profile, state, diagnostics)
