"""Medium Stage 1: manual sessions and fail-closed, read-only UI discovery.

No story, database, browser-storage inspection, or content-writing API belongs here.
Navigation candidates are hypotheses until found in the live DOM. Only observed
anchor destinations or an explicitly identified account menu can be activated.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from pathlib import Path
import re
from time import monotonic
from types import SimpleNamespace
from urllib.parse import unquote, urljoin, urlsplit
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError

from ..config import runtime_paths
from ..errors import BrowserSessionError, ConfigurationError
from .session import persistent_browser, private_directory, safe_screenshot

HOME = "https://medium.com/"
ME = "https://medium.com/me"
PRECURSOR_PATH_PATTERN = re.compile(r"^/cdn-cgi/challenge-platform/h/[^/]+/precursor/")
PRECURSOR_SAFE_PATH = "/cdn-cgi/challenge-platform/h/[variant]/precursor/"
SECURITY_VERIFICATION = "EXPECTED SECURITY VERIFICATION"
AUTHENTICATION_WAIT_SECONDS = 8
VERIFICATION_RENDER_SECONDS = 1
ROLES = ("button", "link", "menuitem")
LABELS = (
    "Sign in", "Sign up", "Get started", "Welcome back", "Sign out", "Settings",
    "Stories", "Your stories", "Library", "Profile", "Import a story", "Import story",
    "Import", "Cancel", "Back", "Close", "User menu", "Account menu", "Profile menu",
    "User options menu", "Open user menu", "Open account menu", "Open profile menu",
    "Edit profile", "Write",
)
OWNER_LABELS = ("Edit profile", "Sign out", "Settings", "Stories", "Your stories",
                "Write", "User menu", "Account menu", "Profile menu", "User options menu",
                "Open user menu", "Open account menu", "Open profile menu")
SIGNED_OUT_LABELS = ("Sign in", "Sign up", "Get started", "Welcome back")
MENU_NAMES = re.compile(
    r"^(?:(?:open )?(?:user|account|profile)(?: options)? menu)$", re.I,
)
IMPORT_NAMES = re.compile(r"^Import (?:a )?story$", re.I)
STORIES_NAMES = re.compile(r"^(?:Your )?Stories$", re.I)
# Page URLs keep only fixed public routes. Request diagnostics separately retain
# endpoint structure via safe_endpoint_path, without opaque identifiers/secrets.
SAFE_PATHS = {
    "/", "/me", "/m/signin", "/m/signup", "/m/signout", "/me/stories", "/me/stories/drafts",
    "/me/stories/public", "/me/settings", "/p/import", "/_/graphql",
    "/_/api/users/me", "/_/api/posts/import", "/_/api/posts",
    "/_/api/telemetry", "/_/api/analytics", "/_/api/events", "/cdn-cgi/rum",
}
TELEMETRY = {
    ("https://www.google-analytics.com", "/g/collect"),
    ("https://www.google-analytics.com", "/collect"),
    ("https://region1.google-analytics.com", "/g/collect"),
    ("https://cloudflareinsights.com", "/cdn-cgi/rum"),
    # Cloudflare's proxied-site RUM destination, observed on Medium home.
    # https://developers.cloudflare.com/web-analytics/data-metrics/data-origin-and-collection/
    # Classification only: this exact POST remains blocked, like all telemetry.
    ("https://medium.com", "/cdn-cgi/rum"),
}
RESOURCE_TYPES = {
    "document", "stylesheet", "image", "media", "font", "script", "texttrack",
    "xhr", "fetch", "eventsource", "websocket", "manifest", "other", "ping",
}


class AuthenticationState(str, Enum):
    AUTHENTICATED = "AUTHENTICATED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    UNKNOWN = "UNKNOWN"
    RATE_LIMITED = "RATE_LIMITED"


class InspectionStopped(BrowserSessionError):
    """Only fixed safe messages; raw browser exceptions must never be attached."""


def trusted(url: str) -> bool:
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.hostname in {"medium.com", "www.medium.com"}
                and p.port in (None, 443) and p.username is None and p.password is None)
    except ValueError:
        return False


def safe_url(raw: str) -> str:
    try:
        p = urlsplit(raw)
        if not trusted(raw):
            return "[unrecognized URL redacted]"
        path = (p.path if p.path in SAFE_PATHS else "/@[profile redacted]"
                if re.fullmatch(r"/@[^/]+/?", p.path) else "/[path redacted]")
        return f"https://{p.hostname}" + path
    except ValueError:
        return "[unrecognized URL redacted]"


def safe_endpoint_path(path: str) -> str:
    """Keep endpoint structure, redacting only potentially sensitive segments.

    This is for request identity, not page URLs or arbitrary UI text. Never
    receive a query, body, header, cookie, or browser-storage value here.
    """
    precursor = PRECURSOR_PATH_PATTERN.match(path)
    if precursor:
        # The internal variant and all trailing identifiers are opaque, even short tokens.
        return PRECURSOR_SAFE_PATH + "/".join(
            "[redacted]" if segment else "" for segment in path[precursor.end():].split("/"))
    segments = []
    redact_next = False
    for segment in (path or "/").split("/"):
        decoded = unquote(segment)
        sensitive_key = bool(re.fullmatch(
            r"(?:token|access[_-]?token|refresh[_-]?token|secret|password|code|"
            r"authorization|session|session[_-]?id|email)", decoded, re.I))
        sensitive = (
            redact_next
            or bool(re.search(r"(?:token|secret|password|authorization)", decoded, re.I))
            or bool(re.search(r"[@=;%\s/?#\\]", decoded))
            or bool(re.search(r"[\x00-\x1f\x7f]", decoded))
            or bool(re.fullmatch(r"[0-9a-f-]{12,}", decoded, re.I))
            or len(decoded) >= 24
        )
        segments.append("[redacted]" if redact_next or (sensitive and not sensitive_key) else segment)
        redact_next = sensitive_key and not redact_next
    return "/".join(segments)


def endpoint_identity(raw: str) -> tuple[str, str, str, str]:
    """Return scheme, hostname, origin and path without URL credentials."""
    try:
        p = urlsplit(raw)
        if p.scheme not in {"http", "https"} or not p.hostname:
            raise ValueError
        hostname = p.hostname
        origin = f"{p.scheme}://{hostname}"
        if p.port is not None and p.port != (443 if p.scheme == "https" else 80):
            origin += f":{p.port}"
        return p.scheme, hostname, origin, safe_endpoint_path(p.path)
    except ValueError:
        return "[redacted]", "[redacted]", "[origin redacted]", "/[redacted]"


def safe_text(raw: str | None) -> str:
    """Sanitize scoped UI metadata; never use on browser storage or request data."""
    if not raw:
        return ""
    value = " ".join(raw.split())
    value = re.sub(r"https?://\S+", "[URL redacted]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+", "[email redacted]", value)
    value = re.sub(r"(?i)\b(?:token|access[_-]?token|refresh[_-]?token|secret|password|code|"
                   r"authorization|session(?:[_-]?id)?)\s*[:=]\s*\S+",
                   "[secret redacted]", value)
    value = re.sub(r"\b[\w-]{24,}\b", "[identifier redacted]", value)
    return value[:700]


def medium_profile(root: Path) -> Path:
    paths = runtime_paths(root)
    profile = paths.medium_browser_profile
    resolved, substack = profile.resolve(), paths.substack_browser_profile.resolve()
    if (resolved.is_relative_to(root.resolve()) or resolved.is_relative_to(substack)
            or substack.is_relative_to(resolved)):
        raise ConfigurationError("Medium requires an external browser profile isolated from Substack.")
    # Reject aliases anywhere in an existing profile, including a linked cookie DB.
    if profile.exists() and any(p.is_symlink() for p in profile.rglob("*")
                                if p.name not in {"SingletonLock", "SingletonSocket", "SingletonCookie"}):
        raise ConfigurationError("Medium browser profile contains an unsafe filesystem alias.")
    return profile


@dataclass
class PrecursorActivity:
    observed: int = 0
    allowed: int = 0
    completed: int = 0
    failed: int = 0


def is_precursor(request) -> bool:
    """The observed security path family only; never read bodies or credentials.

    https://developers.cloudflare.com/cloudflare-challenges/precursor/
    Only the single non-empty variant segment varies within the fixed structure.
    This exception is independent of analytics/RUM and authentication evidence.
    """
    try:
        p = urlsplit(request.url)
        return (request.method.upper() == "POST" and p.scheme == "https"
                and p.hostname == "medium.com" and p.port in (None, 443)
                and p.username is None and p.password is None
                and PRECURSOR_PATH_PATTERN.match(p.path) is not None
                and getattr(request, "resource_type", "other") in {"xhr", "fetch"})
    except ValueError:
        return False


@dataclass
class ReadOnlyGuard:
    requests: list[dict] = field(default_factory=list)
    stopped: bool = False
    rate_limited: bool = False
    stage: str = "SESSION_CHECK"
    precursor: PrecursorActivity = field(default_factory=PrecursorActivity)
    _precursor_pending: set[int] = field(default_factory=set, repr=False)
    _precursor_completed_at: float | None = field(default=None, repr=False)

    @property
    def verification_pending(self) -> bool:
        return bool(self._precursor_pending)

    @property
    def verification_settled(self) -> bool:
        return (not self.verification_pending
                and (self._precursor_completed_at is None
                     or monotonic() - self._precursor_completed_at >= VERIFICATION_RENDER_SECONDS))

    def handle(self, route) -> None:
        request = route.request
        method = request.method.upper()
        scheme, hostname, origin, safe_path = endpoint_identity(request.url)
        resource_type = getattr(request, "resource_type", "other")
        if is_precursor(request):
            allowed = not self.stopped
            self.precursor.observed += 1
            item = {"method": method, "origin": origin, "path": safe_path,
                    "resource_type": resource_type, "stage": self.stage,
                    "classification": SECURITY_VERIFICATION,
                    "disposition": "ALLOWED" if allowed else "BLOCKED"}
            if item not in self.requests:
                self.requests.append(item)
            if allowed:
                self.precursor.allowed += 1
                # Track lifecycle by transient object identity, never URL/token data.
                self._precursor_pending.add(id(request))
                route.continue_()
            else:
                route.abort()
            return
        try:
            p = urlsplit(request.url)
            path = p.path or "/"
            telemetry = (method == "POST" and p.username is None and p.password is None
                         and (origin, path) in TELEMETRY)
            # GET is not sufficient proof of safety for an import/content endpoint.
            dangerous = trusted(request.url) and (
                (path.startswith(("/_/", "/api/")) and bool(re.search(
                    r"(?:import|create|publish|delete|submit|save|draft|signout)", path, re.I)))
                or (path.rstrip("/") == "/p/import"
                    and (bool(p.query) or self.stage == "SESSION_CHECK"))
                or path in {"/new-story", "/m/signout"}
            )
        except ValueError:
            telemetry, dangerous = False, True
        if method in {"GET", "HEAD", "OPTIONS"} and not dangerous and not self.stopped:
            route.continue_()
            return
        classification = "TELEMETRY_BLOCKED" if telemetry else "UNEXPECTED_REQUEST_BLOCKED"
        # Do not turn every subsequently blocked read into a new mutation report.
        if method not in {"GET", "HEAD", "OPTIONS"} or dangerous:
            item = {"method": method if method in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"} else "OTHER",
                    "scheme": scheme, "hostname": hostname, "origin": origin,
                    "path": safe_path,
                    "resource_type": resource_type if resource_type in RESOURCE_TYPES else "other",
                    "stage": self.stage,
                    "classification": classification, "disposition": "BLOCKED"}
            if item not in self.requests:
                self.requests.append(item)
        if not telemetry:
            self.stopped = True
        route.abort()

    def request_finished(self, request) -> None:
        """HTTP exchange completed; this says nothing about authentication."""
        if id(request) in self._precursor_pending:
            self._precursor_pending.remove(id(request))
            self.precursor.completed += 1
            self._precursor_completed_at = monotonic()

    def request_failed(self, request) -> None:
        if id(request) in self._precursor_pending:
            self._precursor_pending.remove(id(request))
            self.precursor.failed += 1

    def response(self, response) -> None:
        if response.status == 429 and trusted(response.url):
            self.rate_limited = True
            self.stopped = True

    def check(self) -> None:
        if self.rate_limited:
            raise InspectionStopped("Medium returned HTTP 429; stopped without retrying.")
        if self.stopped:
            raise InspectionStopped("Unexpected request blocked; read-only inspection stopped. No allowlist was expanded.")


def visible(scope, role: str, name) -> list:
    return [item for item in scope.get_by_role(role, name=name).all() if item.is_visible()]


def has(page, label: str) -> bool:
    pattern = re.compile("^" + re.escape(label) + "$", re.I)
    return any(visible(page, role, pattern) for role in ROLES)


@dataclass
class AccountProbe:
    """Only sanitized navigation and rendered evidence; no browser credentials."""

    requested_url: str = "[not requested]"
    final_url: str = "[unavailable]"
    redirect_chain: list[dict] = field(default_factory=list)
    navigation: list[str] = field(default_factory=list)
    page_title: str = ""
    owner_controls: tuple[str, ...] = ()
    signed_out_controls: tuple[str, ...] = ()
    completed: bool = False
    ambiguous: bool = False

    def response(self, page, response) -> None:
        request = response.request
        if request.is_navigation_request() and request.frame == page.main_frame:
            self.redirect_chain.append({"url": safe_url(response.url),
                                        "method": request.method if request.method == "GET" else "OTHER",
                                        "status": response.status})
            if (not trusted(response.url) or request.method != "GET"
                    or response.status >= 400):
                self.ambiguous = True

    def navigated(self, page, frame) -> None:
        if frame == page.main_frame:
            self.navigation.append(safe_url(frame.url))
            if not trusted(frame.url):
                self.ambiguous = True

    def capture(self, page, evidence: tuple[str, ...]) -> None:
        self.final_url = safe_url(page.url)
        self.page_title = safe_text(page.title())
        self.owner_controls = tuple(label for label in evidence if label in OWNER_LABELS)
        self.signed_out_controls = tuple(label for label in evidence
                                        if label in SIGNED_OUT_LABELS or label == "Welcome back (visible heading)")


def authentication(page, guard: ReadOnlyGuard, probe: AccountProbe | None = None
                   ) -> tuple[AuthenticationState, tuple[str, ...]]:
    if guard.rate_limited:
        return AuthenticationState.RATE_LIMITED, ("Medium HTTP 429",)
    if not trusted(page.url):
        return AuthenticationState.UNKNOWN, ("Page origin is not verified Medium",)
    rate = page.get_by_text(re.compile(r"^\s*Too many requests\s*$", re.I))
    if any(item.is_visible() for item in rate.all()):
        guard.rate_limited = guard.stopped = True
        return AuthenticationState.RATE_LIMITED, ("Visible Too many requests message",)
    labels = tuple(label for label in LABELS if has(page, label))
    welcome = bool(visible(page, "heading", re.compile(r"^Welcome back\.?$", re.I)))
    evidence = labels + (("Welcome back (visible heading)",) if welcome else ())
    negative = any(label in labels for label in ("Sign in", "Get started")) or welcome
    # A personalized avatar, cookies, Stories link, or signed-in-looking URL alone
    # proves nothing. Require the explicit sign-out control AND account navigation.
    positive = "Sign out" in labels and any(label in labels for label in ("Settings", "Stories", "Your stories"))
    if probe is not None:
        probe.capture(page, evidence)
        path = urlsplit(page.url).path
        profile = re.fullmatch(r"/@[^/]+/?", path) is not None
        login = path.rstrip("/") in {"/m/signin", "/m/signup"}
        positive = positive or (profile and "Edit profile" in labels)
        negative = bool(probe.signed_out_controls)
        # A profile route with signed-out UI is ambiguous even without owner UI.
        # A sign-in destination with account UI is also contradictory.
        if (probe.requested_url != ME or not probe.completed or not probe.navigation
                or not probe.redirect_chain or probe.ambiguous
                or (profile and negative) or (login and positive)):
            return AuthenticationState.UNKNOWN, evidence
    if guard.rate_limited:
        return AuthenticationState.RATE_LIMITED, ("Medium HTTP 429",)
    if guard.stopped or positive == negative:
        return AuthenticationState.UNKNOWN, evidence
    return (AuthenticationState.AUTHENTICATED if positive else AuthenticationState.NOT_AUTHENTICATED), evidence


def open_account_menu(page, guard: ReadOnlyGuard) -> bool:
    candidates = visible(page, "button", MENU_NAMES)
    if len(candidates) > 1:
        raise InspectionStopped("Ambiguous account-menu navigation; stopped.")
    if not candidates:
        return False
    button = candidates[0]
    # A name alone cannot establish that a button is a non-submitting menu toggle.
    props = button.evaluate("e => ({popup:e.getAttribute('aria-haspopup'), expanded:e.getAttribute('aria-expanded'), type:e.type, form:!!e.form})")
    if props["form"] or (props["popup"] not in {"menu", "true"} and props["expanded"] not in {"true", "false"}):
        raise InspectionStopped("Account-menu control lacks unambiguous menu semantics; stopped.")
    guard.check()
    if props["expanded"] != "true":
        button.click()
        settle(page, guard)
    return True


def settle(page, guard: ReadOnlyGuard, timeout: float = 2) -> None:
    deadline = monotonic() + timeout
    while True:
        guard.check()
        if trusted(page.url) and any(item.is_visible() for item in page.get_by_text(
                re.compile(r"^\s*Too many requests\s*$", re.I)).all()):
            guard.rate_limited = guard.stopped = True
            guard.check()
        if monotonic() >= deadline:
            return
        page.wait_for_timeout(100)


def navigate(page, url: str, guard: ReadOnlyGuard) -> None:
    guard.check()
    page.goto(url, wait_until="domcontentloaded")
    settle(page, guard)
    if not trusted(page.url):
        raise InspectionStopped("Navigation left the verified Medium origin; stopped.")


def verify(page, guard: ReadOnlyGuard) -> tuple[AuthenticationState, tuple[str, ...], bool]:
    navigate(page, HOME, guard)
    # Security transport and blocked telemetry are not authentication signals.
    # Allow observed verification to finish and controls to hydrate boundedly.
    deadline = monotonic() + AUTHENTICATION_WAIT_SECONDS
    opened = False
    while True:
        guard.check()
        state, evidence = authentication(page, guard)
        if (state != AuthenticationState.UNKNOWN and guard.verification_settled) or monotonic() >= deadline:
            return state, evidence, opened
        if not opened:
            opened = open_account_menu(page, guard)
            if opened:
                continue
        page.wait_for_timeout(100)


def guard_navigation_redirects(page, guard: ReadOnlyGuard) -> None:
    """Apply the same policy to every document hop, including HTTP redirects.

    Playwright routing can skip redirected requests. Chromium's request-stage
    interception pauses each destination before transport. Read only method/URL;
    never inspect or serialize the event's headers or body. Keep it enabled until
    context closure so late navigation cannot escape the guard.
    """
    channel = page.context.new_cdp_session(page)

    def paused(event):
        request = event["request"]
        guard.handle(SimpleNamespace(
            request=SimpleNamespace(method=request["method"], url=request["url"], resource_type="document"),
            continue_=lambda: channel.send("Fetch.continueRequest", {"requestId": event["requestId"]}),
            abort=lambda: channel.send("Fetch.failRequest", {
                "requestId": event["requestId"], "errorReason": "BlockedByClient"}),
        ))

    channel.on("Fetch.requestPaused", paused)
    channel.send("Fetch.enable", {"patterns": [
        {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]})


def wait_for_verification(page, guard: ReadOnlyGuard) -> None:
    deadline = monotonic() + AUTHENTICATION_WAIT_SECONDS
    page.wait_for_timeout(100)
    while True:
        guard.check()
        if guard.precursor.failed:
            raise InspectionStopped("Security verification failed; account probe stopped.")
        if guard.verification_settled:
            return
        if monotonic() >= deadline:
            raise InspectionStopped("Security verification did not settle within the observation window; stopped.")
        page.wait_for_timeout(100)


def verify_account_probe(page, guard: ReadOnlyGuard, probe: AccountProbe
                         ) -> tuple[AuthenticationState, tuple[str, ...], bool]:
    guard_navigation_redirects(page, guard)
    navigate(page, HOME, guard)
    wait_for_verification(page, guard)
    guard.check()
    probe.requested_url = ME
    page.on("response", lambda response: probe.response(page, response))
    page.on("framenavigated", lambda frame: probe.navigated(page, frame))
    # goto is a GET navigation. No destination is assumed or directly requested.
    navigate(page, ME, guard)
    probe.completed = True
    deadline = monotonic() + AUTHENTICATION_WAIT_SECONDS
    opened = False
    while True:
        guard.check()
        state, evidence = authentication(page, guard, probe)
        if guard.precursor.failed:
            raise InspectionStopped("Security verification failed; account probe stopped.")
        if guard.verification_settled and state != AuthenticationState.UNKNOWN:
            return state, evidence, opened
        if monotonic() >= deadline:
            if not guard.verification_settled:
                raise InspectionStopped("Security verification did not settle within the observation window; stopped.")
            return state, evidence, opened
        if guard.verification_settled and not opened and not probe.signed_out_controls:
            opened = open_account_menu(page, guard)
            if opened:
                continue
        page.wait_for_timeout(100)


@dataclass
class MediumResult:
    profile: Path
    authentication: AuthenticationState = AuthenticationState.UNKNOWN
    evidence: tuple[str, ...] = ()
    current_url: str = "[unavailable]"
    navigation: list[str] = field(default_factory=list)
    interface: dict | None = None
    last_read_only: str = "No live Medium page verified"
    first_write: str = "Unverified; URL entry and import actions are prohibited in Stage 1"
    stopped_reason: str = ""
    requests: list[dict] = field(default_factory=list)
    precursor: PrecursorActivity = field(default_factory=PrecursorActivity)
    account_probe: AccountProbe | None = None
    diagnostics: str | None = None


def observed_link(page, pattern) -> tuple[str, str] | None:
    items = visible(page, "link", pattern)
    if not items:
        return None
    if len(items) != 1:
        raise InspectionStopped("Ambiguous navigation links; stopped.")
    item = items[0]
    raw = item.get_attribute("href") or ""
    target = urljoin(page.url, raw)
    try:
        p = urlsplit(target)
    except ValueError:
        raise InspectionStopped("Navigation destination is invalid; stopped.") from None
    # Only simple, observed same-origin page links: no redirect/source/token query,
    # API endpoint, editor, opaque identifiers, or content-creation destination.
    if (not trusted(target) or p.query or p.fragment or item.get_attribute("download") is not None
            or not re.fullmatch(r"/(?:[a-z-]+/)*[a-z-]+/?", p.path)
            or re.search(r"(?:^|[/_-])(?:api|new|create|publish|delete|submit|signout|edit)(?:[/_-]|$)", p.path)):
        raise InspectionStopped("Navigation destination is not demonstrably read-only; stopped.")
    return safe_text(item.inner_text()), target


def inspect_interface(page) -> dict:
    headings = visible(page, "heading", IMPORT_NAMES)
    if len(headings) != 1:
        raise InspectionStopped("A unique Import a story heading was not found; stopped.")
    inputs = [i for i in page.locator('input:not([type="hidden"]), textarea').all() if i.is_visible()]
    candidates = []
    for element in inputs:
        data = element.evaluate("""e => ({
            type:e.getAttribute('type') || 'text', placeholder:e.getAttribute('placeholder') || '',
            aria:e.getAttribute('aria-label') || '',
            labelledby:(e.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim(),
            labels:Array.from(e.labels || []).map(x=>x.textContent).join(' ').trim(),
            title:e.getAttribute('title') || '', testid:e.getAttribute('data-testid'),
            empty:e.value === ''
        })""")
        hint = " ".join(str(data.get(key) or "") for key in ("placeholder", "aria", "labelledby", "labels"))
        if data["type"] == "url" or (data["type"] == "text" and re.search(r"\b(?:url|link)\b|https?://", hint, re.I)):
            candidates.append((element, data))
    if len(candidates) != 1:
        raise InspectionStopped("A unique source URL input was not found; stopped without changing any field.")
    _, data = candidates[0]
    if not data["empty"]:
        raise InspectionStopped("Source URL field is not empty; stopped without reading or clearing its value.")
    actions = visible(page, "button", re.compile(r"^(?:Import|Import story|Import a story|Submit)$", re.I))
    if len(actions) != 1:
        raise InspectionStopped("A unique import action was not found; stopped without clicking.")
    action = actions[0]
    # Playwright's accessibility snapshot preserves computed names, including
    # placeholders, while the field is known empty. Never snapshot the whole page.
    snapshot = candidates[0][0].aria_snapshot()
    name_match = re.search(r'^- (?:textbox|searchbox)(?: "((?:\\.|[^"\\])*)")?', snapshot)
    accessible_name = (name_match.group(1) or "") if name_match else "[unavailable]"
    exits = [label for label in ("Cancel", "Back", "Close") if has(page, label)]
    copy = []
    for element in page.locator("p").all():
        if element.is_visible():
            text = element.inner_text()
            if len(text) <= 1500 and re.search(r"\b(import|canonical|originally published)\b", text, re.I):
                copy.append(safe_text(text))
    return {
        "heading": safe_text(headings[0].inner_text()),
        "url_input": "One visible, empty source URL input; never modified",
        "input_accessible_name": safe_text(accessible_name),
        "input_placeholder": safe_text(data["placeholder"]), "input_type": safe_text(data["type"]),
        "input_testid": safe_text(data["testid"]) if data["testid"] is not None else None,
        "import_action_label": safe_text(action.inner_text()),
        "import_action_testid": safe_text(action.get_attribute("data-testid")) or None,
        "import_action_enabled_while_empty": action.is_enabled(),
        "safe_cancel_back": exits or ["Browser Back to previously verified page (not activated)"],
        "explanatory_text": copy, "canonical_text": [t for t in copy if re.search(r"canonical", t, re.I)],
    }


def discover_import(page, guard: ReadOnlyGuard, result: MediumResult) -> None:
    guard.stage = "IMPORT_INSPECTION"
    for _ in range(3):
        guard.check()
        if visible(page, "heading", IMPORT_NAMES):
            result.interface = inspect_interface(page)
            guard.check()
            result.last_read_only = "Inspected the empty URL field and import control without modifying or activating either"
            result.first_write = (
                "Treat entering a source URL as the start of Stage 2; on-input network behavior is unverified. "
                f"The observed '{result.interface['import_action_label']}' control is the apparent import submission boundary. "
                "Article fetching and draft creation after activation were not tested."
            )
            return
        link = observed_link(page, IMPORT_NAMES)
        if link is None:
            link = observed_link(page, STORIES_NAMES)
        if link is None:
            raise InspectionStopped("No unambiguous Stories or Import a story navigation link found; stopped.")
        label, target = link
        if target == page.url:
            raise InspectionStopped("Navigation made no progress toward the Import interface; stopped.")
        navigate(page, target, guard)
        result.navigation.append(f"{label} → {safe_url(page.url)}")
        result.last_read_only = f"Opened observed navigation link: {label}"
    raise InspectionStopped("Import navigation limit reached; stopped.")


def diagnostics(page, directory: Path, result: MediumResult, guard: ReadOnlyGuard | None = None) -> str | None:
    try:
        private_directory(directory)
        screenshot = safe_screenshot(page, directory, platform="medium")
        controls = [label for label in LABELS if has(page, label)]
        if guard is not None and result.account_probe is not None:
            state, result.evidence = authentication(page, guard, result.account_probe)
            if state == AuthenticationState.RATE_LIMITED:
                result.authentication = state
            elif (state != result.authentication or not guard.verification_settled
                  or guard.precursor.failed):
                result.authentication = AuthenticationState.UNKNOWN
            result.account_probe.capture(page, result.evidence)
        if guard is not None and guard.stopped:
            # Screenshot/DOM reads pump browser events too. Include late blocked
            # activity and never report success after an interrupted inspection.
            result.authentication = (AuthenticationState.RATE_LIMITED if guard.rate_limited
                                     else AuthenticationState.UNKNOWN)
            result.stopped_reason = ("Medium rate limited; stopped without retrying." if guard.rate_limited else
                                     "Unexpected request blocked; stopped without expanding the allowlist.")
        if guard is not None:
            result.requests = list(guard.requests)
        result.current_url = safe_url(page.url)
        target = directory / f"medium-{uuid4().hex}.json"
        with target.open("x") as handle:
            target.chmod(0o600)
            json.dump({**asdict(result), "profile": str(result.profile),
                       "authentication": result.authentication.value,
                       "safe_visible_controls": controls,
                       "screenshot": str(screenshot) if screenshot else None}, handle, indent=2)
        return str(target)
    except (OSError, PlaywrightError):
        return None


def run_medium(root: Path, *, complete_login: Callable[[], None] | None = None,
               inspect_import: bool = False) -> MediumResult:
    """All Medium entry points; no database/config/story reads or secret exports."""
    if complete_login is not None and inspect_import:
        raise ValueError("Manual login and import inspection must be separate commands.")
    paths = runtime_paths(root)
    result = MediumResult(medium_profile(root))
    if complete_login is None and not inspect_import:
        result.account_probe = AccountProbe()
    guard = ReadOnlyGuard()
    result.precursor = guard.precursor
    with persistent_browser(result.profile, paths.diagnostics, platform="Medium",
                            read_only=complete_login is None,
                            guarded_publication=complete_login is not None,
                            request_guard=guard.handle) as context:
        page = context.new_page()
        if complete_login is not None:
            page.goto(HOME, wait_until="domcontentloaded")
            complete_login()
            # Close manually opened tabs before installing the inspection guard;
            # do not reuse any login/callback tab or examine its URL/storage.
            page = context.new_page()
            for old in list(context.pages):
                if old != page:
                    old.close()
            context.route("**/*", guard.handle)
        context.on("response", guard.response)
        context.on("requestfinished", guard.request_finished)
        context.on("requestfailed", guard.request_failed)
        try:
            if result.account_probe is not None:
                result.authentication, result.evidence, menu_opened = verify_account_probe(page, guard, result.account_probe)
                result.last_read_only = "Navigated GET /me and inspected the actual destination and rendered account controls"
                result.navigation = ["Medium home", "GET /me"]
            else:
                result.authentication, result.evidence, menu_opened = verify(page, guard)
                result.last_read_only = "Loaded Medium home and inspected rendered authentication controls"
                result.navigation = ["Medium home"]
            if menu_opened:
                result.navigation.append("Account menu (observed menu toggle)")
            if inspect_import:
                if result.authentication != AuthenticationState.AUTHENTICATED:
                    raise InspectionStopped("Import inspection requires positively verified live authentication.")
                discover_import(page, guard, result)
            guard.check()
        except (InspectionStopped, PlaywrightError) as exc:
            # Recompute safe authentication evidence even when the network blocked
            # hydration, but never turn an interrupted check into authentication.
            result.authentication, result.evidence = authentication(page, guard, result.account_probe)
            result.stopped_reason = ("Medium rate limited; stopped without retrying." if guard.rate_limited else
                                     "Unexpected request blocked; stopped without expanding the allowlist." if guard.stopped else
                                     str(exc) if isinstance(exc, InspectionStopped) else
                                     "UI or navigation could not be verified safely; stopped. See safe diagnostics.")
            if not guard.rate_limited and not inspect_import:
                result.authentication = AuthenticationState.UNKNOWN
        result.current_url = safe_url(page.url)
        result.requests = guard.requests
        result.diagnostics = diagnostics(page, paths.diagnostics, result, guard)
    return result
