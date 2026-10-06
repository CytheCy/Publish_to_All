from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Error as PlaywrightError

from publish_to_all.browser import session, substack
from publish_to_all.browser.substack import AuthenticationState as Auth, SessionResult
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, ConfigurationError
from publish_to_all.presentation import format_substack_session


URL = "https://example.substack.com"


class Elements:
    def __init__(self, entries=()):
        self.entries = list(entries)

    def get_by_role(self, role, *, name=None):
        return Elements([entry for entry in self.entries
                         if entry[0] == role and (name is None or name.search(entry[1]))])

    def or_(self, other):
        return Elements(self.entries + other.entries)

    def all(self):
        return [MagicMock(is_visible=MagicMock(return_value=entry[2])) for entry in self.entries]


class RenderedPage(Elements):
    def __init__(self, entries=(), navigation=(), url=URL):
        super().__init__(entries)
        self.navigation = navigation
        self.url = url

    def get_by_role(self, role, *, name=None):
        if role in ("navigation", "menu"):
            return Elements(self.navigation)
        return super().get_by_role(role, name=name)


@pytest.mark.parametrize("entries,navigation,state", [
    ([], [], Auth.UNKNOWN),
    ([("button", "Sign out", True)], [], Auth.AUTHENTICATED),
    ([("menuitem", "Log out", True)], [], Auth.AUTHENTICATED),
    ([("button", "Sign out", False)], [], Auth.UNKNOWN),
    ([("link", "Sign in", True)], [], Auth.NOT_AUTHENTICATED),
    ([("heading", "Sign in to Substack", True)], [], Auth.NOT_AUTHENTICATED),
    ([("button", "Sign out", True), ("link", "Sign in", True)], [], Auth.UNKNOWN),
    ([("button", "Account menu", True)], [], Auth.UNKNOWN),
    ([], [("link", "Dashboard", True)], Auth.UNKNOWN),
    ([("button", "Account menu", True)], [("link", "Dashboard", True)], Auth.AUTHENTICATED),
    ([], [("link", label, True) for label in ("Posts", "Subscribers", "Stats")], Auth.AUTHENTICATED),
    ([("heading", "Dashboard", True)], [], Auth.UNKNOWN),
])
def test_authentication_evidence(entries, navigation, state):
    assert substack.detect_authentication(RenderedPage(entries, navigation), URL) == state


@pytest.mark.parametrize("url,trusted", [
    (URL + "/publish/home", True), ("https://substack.com/home", True),
    ("https://www.substack.com/account", True),
    ("https://other.substack.com/", True), ("https://stories.example.org/", True),
    ("https://substack.com.evil.example/", False), ("https://evilsubstack.com/", False),
    ("http://substack.com/", False), ("https://user:secret@substack.com/", False),
    ("https://substack.com:8443/", False), ("about:blank", False),
])
def test_redirect_origin_guard(url, trusted):
    assert substack.trusted_page(url, "https://stories.example.org") == trusted
    page = RenderedPage([("button", "Sign out", True)], url=url)
    assert substack.detect_authentication(page, "https://stories.example.org") == (
        Auth.AUTHENTICATED if trusted else Auth.UNKNOWN)


def test_profile_and_diagnostics_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("XDG_STATE_HOME")
    paths = runtime_paths(tmp_path / "project")
    assert paths.substack_browser_profile == tmp_path / "home/.local/state/publish-to-all/browser-profile/substack"
    assert paths.diagnostics == tmp_path / "home/.local/state/publish-to-all/diagnostics"
    assert not paths.state.exists()


def test_profile_and_diagnostics_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "external"))
    paths = runtime_paths(tmp_path / "project")
    assert paths.substack_browser_profile == tmp_path / "external/publish-to-all/browser-profile/substack"
    assert paths.diagnostics == tmp_path / "external/publish-to-all/diagnostics"


@pytest.mark.parametrize("child", ["browser-profile/substack", "diagnostics"])
def test_browser_paths_reject_symlink_into_project(tmp_path, monkeypatch, child):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    link = external / "publish-to-all" / child
    link.parent.mkdir(parents=True)
    link.symlink_to(project, target_is_directory=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(external))
    with pytest.raises(ConfigurationError):
        runtime_paths(project)


@pytest.mark.parametrize("state,label", [
    (Auth.AUTHENTICATED, "Authenticated"), (Auth.NOT_AUTHENTICATED, "Not authenticated"),
    (Auth.UNKNOWN, "Unknown (could not verify authentication)"),
    (Auth.RATE_LIMITED, "Temporarily rate limited"),
])
def test_session_format(state, label, tmp_path):
    diagnostics = None
    if state == Auth.RATE_LIMITED:
        diagnostics = substack.SessionDiagnostics(
            URL + "/publish/home", "Too many requests", (), None, True,
        )
    result = SessionResult(URL, tmp_path / "profile", state, diagnostics)
    output = format_substack_session(result)
    assert f"Authentication: {label}" in output
    assert URL in output and str(result.profile) in output
    assert "No draft was created.\nNothing was published." in output
    assert ("substack-login" in output) == (state in (Auth.UNKNOWN, Auth.NOT_AUTHENTICATED))
    assert ("reusable authenticated" in output) == (state == Auth.AUTHENTICATED)
    assert ("Substack is refusing requests" in output) == (state == Auth.RATE_LIMITED)
    assert ("Rate limiting detected:\nYes" in output) == (state == Auth.RATE_LIMITED)
    assert Auth(state.value) is state


@pytest.mark.parametrize("command", ["substack-login", "substack-session"])
def test_missing_config_does_not_launch(command, tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    launch = MagicMock()
    monkeypatch.setattr(substack, "persistent_browser", launch)
    assert main([command]) == 1
    assert "Set [substack].publication_url" in capsys.readouterr().err
    launch.assert_not_called()
    assert not runtime_paths(project).database.exists()


@pytest.fixture
def mocked_browser(monkeypatch):
    driver = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = driver
    monkeypatch.setattr(session, "sync_playwright", factory)
    context = driver.chromium.launch_persistent_context.return_value
    page = context.new_page.return_value
    context.pages = [page]
    page.is_closed.return_value = False
    page.url = URL
    page.title.return_value = "Substack"
    return driver, context, page


def test_persistent_browser_headed_and_closed(tmp_path, mocked_browser):
    driver, context, _ = mocked_browser
    profile = tmp_path / "profile"
    with session.persistent_browser(profile, tmp_path / "diagnostics") as actual:
        assert actual is context
    driver.chromium.launch_persistent_context.assert_called_once_with(
        user_data_dir=str(profile), headless=False, accept_downloads=False)
    context.close.assert_called_once()
    assert profile.stat().st_mode & 0o777 == 0o700


def test_guarded_publication_prevents_service_worker_and_socket_bypasses(tmp_path, mocked_browser):
    driver, context, _ = mocked_browser
    with session.persistent_browser(tmp_path / 'profile', tmp_path / 'diagnostics',
                                    guarded_publication=True):
        assert driver.chromium.launch_persistent_context.call_args.kwargs['service_workers'] == 'block'
        socket = MagicMock()
        context.route_web_socket.call_args.args[1](socket)
        socket.connect_to_server.assert_not_called()
        # Publication installs the single shared mutation guard on the context.
        context.route.assert_not_called()


@pytest.mark.parametrize("failure", ["navigation", "launch", "screenshot", "close"])
def test_browser_errors_are_sanitized_and_cleanup(tmp_path, mocked_browser, failure):
    driver, context, page = mocked_browser
    secret = "https://substack.com/?token=secret password=secret"
    if failure == "launch":
        driver.chromium.launch_persistent_context.side_effect = PlaywrightError(secret)
    elif failure == "close":
        context.close.side_effect = PlaywrightError(secret)
    elif failure == "screenshot":
        page.screenshot.side_effect = PlaywrightError(secret)
    with pytest.raises(BrowserSessionError) as error:
        with session.persistent_browser(tmp_path / "profile", tmp_path / "diagnostics"):
            if failure != "close":
                raise PlaywrightError(secret)
    assert "secret" not in str(error.value)
    assert "python -m playwright install chromium" in str(error.value)
    if failure != "launch":
        context.close.assert_called_once()
    if failure in ("navigation", "close"):
        target = Path(page.screenshot.call_args.kwargs["path"])
        assert target.exists() and str(target) in str(error.value)
        assert target.stat().st_mode & 0o777 == 0o600
    else:
        assert "Screenshot unavailable" in str(error.value)
        assert not list((tmp_path / "diagnostics").glob("*.png"))


def test_diagnostics_unique(tmp_path, mocked_browser):
    _, context, page = mocked_browser
    session.browser_failure(context, tmp_path)
    session.browser_failure(context, tmp_path)
    assert page.screenshot.call_count == 2
    assert len(list(tmp_path.glob("substack-*.png"))) == 2


def test_profile_permission_error_is_clean(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError("secret")
    monkeypatch.setattr(session, "private_directory", denied)
    with pytest.raises(BrowserSessionError, match="Screenshot unavailable"):
        with session.persistent_browser(tmp_path / "profile", tmp_path / "diagnostics"):
            pytest.fail("Should not yield")


@pytest.mark.parametrize("cancel", [EOFError, KeyboardInterrupt])
def test_cancellation_closes_browser(tmp_path, mocked_browser, cancel):
    _, context, _ = mocked_browser
    with pytest.raises(cancel):
        with session.persistent_browser(tmp_path / "profile", tmp_path / "diagnostics"):
            raise cancel()
    context.close.assert_called_once()


@pytest.mark.parametrize("command", ["substack-login", "substack-session"])
@pytest.mark.parametrize("state", list(Auth))
def test_cli_session_without_story_or_database(tmp_path, monkeypatch, capsys, mocked_browser, command, state):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    (project / "config.toml").write_text(f'[substack]\npublication_url = "{URL}"')
    paths = runtime_paths(project)
    paths.database.parent.mkdir(parents=True)
    paths.database.write_bytes(b"database must remain untouched")
    monkeypatch.setattr(substack, "verify_page", lambda page, url: state)
    enter = MagicMock(return_value="")
    monkeypatch.setattr("builtins.input", enter)
    assert main([command]) == (0 if state == Auth.AUTHENTICATED else 1)
    assert "Nothing was published." in capsys.readouterr().out
    assert paths.database.read_bytes() == b"database must remain untouched"
    _, context, page = mocked_browser
    context.close.assert_called_once()
    assert enter.call_count == (1 if command == "substack-login" else 0)
    if command == "substack-login":
        page.goto.assert_called_once_with(URL + "/publish/home", wait_until="domcontentloaded")
        assert context.new_page.call_count == 2
    # Session code never invokes any mutation API or reads browser secrets.
    page.click.assert_not_called()
    page.fill.assert_not_called()
    page.evaluate.assert_not_called()
    context.cookies.assert_not_called()
    context.storage_state.assert_not_called()


def test_verify_navigates_and_waits_for_hydration(monkeypatch):
    page = MagicMock()
    evidence = MagicMock(side_effect=[substack.Evidence(), substack.Evidence(negative=True),
                                     substack.Evidence(positive=True), substack.Evidence(positive=True)])
    monkeypatch.setattr(substack, "authentication_evidence", evidence)
    monkeypatch.setattr(substack, "monotonic", MagicMock(side_effect=[0, 0, 1, 2, 2, 4]))
    assert substack.verify_page(page, URL, timeout=2) == Auth.AUTHENTICATED
    assert [call.args[0] for call in page.goto.call_args_list] == [URL, URL + "/publish/home"]
    assert page.wait_for_timeout.call_count == 2


@pytest.mark.parametrize("state", [Auth.UNKNOWN, Auth.NOT_AUTHENTICATED])
def test_verify_bounded_wait(monkeypatch, state):
    monkeypatch.setattr(substack, "authentication_evidence", lambda *_: substack.Evidence(
        negative=state == Auth.NOT_AUTHENTICATED))
    page = MagicMock()
    assert substack.verify_page(page, URL, timeout=0) == state
    page.wait_for_timeout.assert_not_called()


def test_http_429_stops_without_retry():
    page = MagicMock(url=URL + "/publish/home")
    response = MagicMock(status=429, url=URL + "/publish/home")
    page.goto.return_value = response
    assert substack.verify_page(page, URL, timeout=0) == Auth.RATE_LIMITED
    page.goto.assert_called_once_with(URL, wait_until="domcontentloaded")
    page.wait_for_timeout.assert_not_called()


def test_rendered_too_many_requests_stops_without_retry():
    page = MagicMock(url=URL)
    response = MagicMock(status=200, url=URL)
    page.goto.return_value = response
    message = MagicMock(is_visible=MagicMock(return_value=True))
    page.get_by_text.return_value.all.return_value = [message]
    page.title.return_value = "Too many requests"
    assert substack.verify_page(page, URL, timeout=0) == Auth.RATE_LIMITED
    assert page.goto.call_count == 1
    page.wait_for_timeout.assert_not_called()


def test_generic_error_page_remains_unknown():
    page = MagicMock(url=URL + "/publish/home")
    page.goto.return_value = MagicMock(status=500, url=URL + "/publish/home")
    page.get_by_text.return_value.all.return_value = []
    page.title.return_value = "Internal Server Error"
    assert substack.verify_page(page, URL, timeout=0, publisher_only=True) == Auth.UNKNOWN
    assert page.goto.call_count == 1


def test_publisher_preflight_uses_one_dashboard_navigation(monkeypatch):
    page = MagicMock()
    evidence = substack.Evidence(positive=True)
    monkeypatch.setattr(substack, "authentication_evidence", MagicMock(return_value=evidence))
    assert substack.verify_page(page, URL, timeout=0, publisher_only=True) == Auth.AUTHENTICATED
    page.goto.assert_called_once_with(URL + "/publish/home", wait_until="domcontentloaded")


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["--help"])
    assert exit.value.code == 0
    output = capsys.readouterr().out
    for command in ("check", "preview", "status", "substack-login", "substack-session"):
        assert command in output


@pytest.mark.parametrize("entries,url,state", [
    ([("button", "Profile", True), ("link", "Creator dashboard", True)], URL, Auth.AUTHENTICATED),
    ([("button", "Avatar", True)], URL, Auth.UNKNOWN),
    ([("button", "Profile", True), ("link", "Inbox", True), ("link", "Library", True)], URL, Auth.AUTHENTICATED),
    ([("textbox", "Email address", True), ("button", "Continue with email", True)], URL, Auth.NOT_AUTHENTICATED),
    ([("textbox", "Email address", True), ("button", "Subscribe", True)], URL, Auth.UNKNOWN),
    ([], "https://substack.com/sign-in?redirect=private", Auth.NOT_AUTHENTICATED),
    ([], "https://substack.com/auth/login", Auth.NOT_AUTHENTICATED),
    ([], "https://evil.example/sign-in", Auth.UNKNOWN),
    ([], URL + "/p/login", Auth.UNKNOWN),
    ([("button", "Sign out", True)], "https://substack.com/sign-in", Auth.UNKNOWN),
    ([("heading", "Dashboard", True), ("link", "Audience", True), ("link", "Settings", True)], URL + "/publish/home", Auth.AUTHENTICATED),
    ([("heading", "Dashboard", True), ("link", "Audience", True), ("link", "Settings", True)], "https://other.substack.com/publish/home", Auth.UNKNOWN),
    ([], URL + "/publish/home", Auth.UNKNOWN),
    ([("heading", "Dashboard", True), ("link", "Audience", True), ("link", "Settings", True), ("button", "Log in", True)], URL + "/publish/home", Auth.UNKNOWN),
])
def test_additional_authentication_signals(entries, url, state):
    assert substack.detect_authentication(RenderedPage(entries, url=url), URL) == state


@pytest.mark.parametrize("first,second,expected", [
    (substack.Evidence(), substack.Evidence(positive=True), Auth.AUTHENTICATED),
    (substack.Evidence(), substack.Evidence(negative=True), Auth.NOT_AUTHENTICATED),
    (substack.Evidence(negative=True), substack.Evidence(positive=True), Auth.UNKNOWN),
    (substack.Evidence(positive=True), substack.Evidence(negative=True), Auth.UNKNOWN),
    (substack.Evidence(True, True), substack.Evidence(positive=True), Auth.UNKNOWN),
    (substack.Evidence(negative=True), substack.Evidence(True, True), Auth.UNKNOWN),
])
def test_two_page_evidence(monkeypatch, first, second, expected):
    monkeypatch.setattr(substack, "authentication_evidence", MagicMock(side_effect=[first, second]))
    page = MagicMock()
    assert substack.verify_page(page, URL, timeout=0) == expected
    assert [call.args[0] for call in page.goto.call_args_list] == [URL, URL + "/publish/home"]
    page.click.assert_not_called()
    page.fill.assert_not_called()


def test_safe_diagnostics_filter_private_values(tmp_path):
    page = RenderedPage([
        ("link", "Home", True), ("button", "Profile", True),
        ("button", "person@example.com", True), ("link", "private draft title", True),
        ("link", "Settings", False),
    ], url=URL + "/publish/home?token=secret#private")
    page.title = lambda: "person@example.com private account"
    page.screenshot = MagicMock()
    result = substack.collect_diagnostics(page, URL, tmp_path)
    assert result.final_url == URL + "/publish/home"
    assert result.title == "[nonstandard page title redacted]"
    assert result.controls == ("Home", "Profile")
    assert result.screenshot.exists()
    assert result.screenshot.stat().st_mode & 0o777 == 0o600
    assert page.screenshot.call_args.kwargs["style"] == session.REDACTED_SCREENSHOT_STYLE
    output = format_substack_session(SessionResult(URL, tmp_path, Auth.UNKNOWN, result))
    for secret in ("secret", "person@", "private draft", "private account"):
        assert secret not in output
    assert str(result.screenshot) in output
    assert "- Home\n- Profile" in output


@pytest.mark.parametrize("url", [URL + "/account/secret?token=secret", "https://secret.evil.example/"])
def test_diagnostics_redact_unrecognized_paths_and_origins(tmp_path, url):
    page = RenderedPage(url=url)
    page.title = lambda: "Dashboard | Substack"
    page.screenshot = MagicMock()
    result = substack.collect_diagnostics(page, URL, tmp_path)
    assert "secret" not in result.final_url
    assert result.title == "Dashboard | Substack"


@pytest.mark.parametrize("state,debug", [(Auth.UNKNOWN, False), (Auth.AUTHENTICATED, True),
                                         (Auth.NOT_AUTHENTICATED, True), (Auth.AUTHENTICATED, False)])
def test_cli_debug_and_unknown_screenshot(tmp_path, monkeypatch, capsys, mocked_browser, state, debug):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    (project / "config.toml").write_text(f'[substack]\npublication_url = "{URL}"')
    monkeypatch.setattr(substack, "verify_page", lambda *_: state)
    assert main(["substack-session", *(["--debug"] if debug else [])]) == (0 if state == Auth.AUTHENTICATED else 1)
    output = capsys.readouterr().out
    _, _, page = mocked_browser
    diagnostic = debug or state == Auth.UNKNOWN
    assert ("Final URL" in output) == diagnostic
    assert ("Page title:" in output) == diagnostic
    assert ("Visible controls:" in output) == diagnostic
    assert page.screenshot.call_count == int(diagnostic)
    if diagnostic:
        path = Path(page.screenshot.call_args.kwargs["path"])
        assert path.exists() and str(path) in output


def test_unknown_capture_failure_keeps_result(tmp_path, monkeypatch, capsys, mocked_browser):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    (project / "config.toml").write_text(f'[substack]\npublication_url = "{URL}"')
    monkeypatch.setattr(substack, "verify_page", lambda *_: Auth.UNKNOWN)
    _, _, page = mocked_browser
    page.screenshot.side_effect = PlaywrightError("token=secret")
    assert main(["substack-session"]) == 1
    output = capsys.readouterr().out
    assert "Authentication: Unknown" in output
    assert "Unavailable (capture failed)" in output
    assert "secret" not in output
    assert not list(runtime_paths(project).diagnostics.glob("*.png"))


@pytest.mark.parametrize("redirect,controls,expected", [
    (URL + "/publish/home", [("link", name, True) for name in ("Posts", "Audience", "Settings")], Auth.AUTHENTICATED),
    ("https://substack.com/sign-in?redirect=secret", [], Auth.NOT_AUTHENTICATED),
    (URL + "/publish/home", [("link", "Posts", True), ("link", "Audience", False), ("link", "Settings", True)], Auth.UNKNOWN),
])
def test_navigation_with_rendered_dashboard_or_login(redirect, controls, expected):
    page = RenderedPage()
    def navigate(url, **kwargs):
        page.url = URL if url == URL else redirect
        page.entries = [] if url == URL else controls
    page.goto = MagicMock(side_effect=navigate)
    assert substack.verify_page(page, URL, timeout=0) == expected
    assert page.goto.call_count == 2


def test_read_only_websocket_does_not_connect_or_deadlock(tmp_path):
    # A subprocess timeout also catches a dispatcher deadlock, where Playwright's
    # own timeouts cannot run. The only endpoint is an isolated localhost socket.
    import subprocess
    import sys
    script = r'''
from pathlib import Path
import socket
import sys
from publish_to_all.browser.session import persistent_browser
root = Path(sys.argv[1])
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(0.1)
    with persistent_browser(root / 'profile', root / 'diagnostics',
                            headless=True, read_only=True) as context:
        page = context.new_page()
        page.evaluate("port => { window.testSocket = new WebSocket('ws://127.0.0.1:' + port); }",
                      listener.getsockname()[1])
        page.wait_for_function('window.testSocket.readyState === WebSocket.OPEN', timeout=3000)
        page.evaluate("window.testSocket.send('isolated test')")
        try:
            connection, address = listener.accept()
        except TimeoutError:
            pass
        else:
            connection.close()
            raise AssertionError('Read-only WebSocket reached the server')
'''
    completed = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
