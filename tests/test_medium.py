"""Local DOM/network fixtures only; never contact Medium or use real profiles."""

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import sync_playwright, Error as PlaywrightError

from publish_to_all.browser import medium, session
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, ConfigurationError
from publish_to_all.medium_presentation import format_medium

Auth = medium.AuthenticationState
ACCOUNT = '<a href="/m/signout">Sign out</a><a href="/me/settings">Settings</a>'
STORIES = '<a href="/me/stories">Stories</a>'
MENU = '''<button aria-label="User options menu" aria-haspopup="menu" aria-expanded="false"
 onclick="document.getElementById('account').hidden=false;this.setAttribute('aria-expanded','true')">Avatar</button>
 <div id="account" hidden>''' + ACCOUNT + STORIES + '</div>'
IMPORT = '''<h1>Import a story</h1><p>Import an existing story. A canonical link points to the original.</p>
 <label for="url">Story URL</label><input id="url" type="url" placeholder="Paste the story link" data-testid="import-url">
 <button type="submit" disabled onclick="window.forbidden=true">Import</button><a href="/me/stories">Cancel</a>
 <script>document.querySelector('input').addEventListener('input',()=>window.forbidden=true);
 document.querySelector('input').addEventListener('change',()=>window.forbidden=true);</script>'''


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    context.route("**/*", lambda route: route.fulfill(status=200, body=""))
    page = context.new_page()
    page.goto(medium.HOME)
    yield page
    context.close()


@pytest.mark.parametrize("html,expected", [
    (ACCOUNT, Auth.AUTHENTICATED), ('<a href="/m/signin">Sign in</a>', Auth.NOT_AUTHENTICATED),
    ('<button>Sign in</button>', Auth.NOT_AUTHENTICATED),
    ('<a href="/m/signup">Get started</a>', Auth.NOT_AUTHENTICATED),
    ('<button hidden>Get started</button>', Auth.UNKNOWN),
    ('<h1>Welcome back.</h1>', Auth.NOT_AUTHENTICATED),
    ('<button>Profile</button>' + STORIES, Auth.UNKNOWN),
    (ACCOUNT + '<button>Sign in</button>', Auth.UNKNOWN),
    (ACCOUNT + '<button>Get started</button>', Auth.UNKNOWN),
    ('<a href="/m/signout">Sign out</a>', Auth.UNKNOWN),
    ('<p>Too many requests</p>', Auth.RATE_LIMITED), ('', Auth.UNKNOWN),
])
def test_live_dom_authentication(page, html, expected):
    page.set_content(html)
    assert medium.authentication(page, medium.ReadOnlyGuard())[0] == expected


def test_cookies_alone_and_untrusted_origin_do_not_authenticate(page):
    page.context.add_cookies([{"name": "session", "value": "secret", "url": medium.HOME}])
    assert medium.authentication(page, medium.ReadOnlyGuard())[0] == Auth.UNKNOWN
    page.goto("https://medium.com.evil.example/")
    page.set_content(ACCOUNT)
    assert medium.authentication(page, medium.ReadOnlyGuard())[0] == Auth.UNKNOWN


def test_http_429_overrides_positive_evidence(page):
    page.set_content(ACCOUNT)
    guard = medium.ReadOnlyGuard()
    guard.response(SimpleNamespace(status=429, url=medium.HOME))
    assert medium.authentication(page, guard)[0] == Auth.RATE_LIMITED
    with pytest.raises(medium.InspectionStopped):
        guard.check()


def test_account_menu_discovered_read_only(page, monkeypatch):
    page.set_content(MENU)
    monkeypatch.setattr(medium, "settle", lambda *_: None)
    assert medium.open_account_menu(page, medium.ReadOnlyGuard())
    assert medium.authentication(page, medium.ReadOnlyGuard())[0] == Auth.AUTHENTICATED


@pytest.mark.parametrize("html", [MENU + MENU, '<button aria-label="User options menu">User</button>',
    '<form><button aria-label="User options menu" aria-haspopup="menu">User</button></form>'])
def test_ambiguous_or_submitting_menu_stops(page, html):
    page.set_content(html)
    with pytest.raises(medium.InspectionStopped):
        medium.open_account_menu(page, medium.ReadOnlyGuard())


def test_interface_is_inspected_without_input_or_import(page):
    page.set_content(IMPORT)
    result = medium.inspect_interface(page)
    assert result["heading"] == "Import a story"
    assert result["input_accessible_name"] == "Story URL"
    assert result["input_placeholder"] == "Paste the story link"
    assert result["input_type"] == "url"
    assert result["input_testid"] == "import-url"
    assert result["import_action_label"] == "Import"
    assert result["import_action_enabled_while_empty"] is False
    assert result["safe_cancel_back"] == ["Cancel"]
    assert result["canonical_text"]
    assert page.locator("input").input_value() == ""
    assert page.evaluate("window.forbidden === undefined")


def test_enabled_import_action_still_never_clicked(page):
    page.set_content(IMPORT.replace("disabled", ""))
    assert medium.inspect_interface(page)["import_action_enabled_while_empty"] is True
    assert page.evaluate("window.forbidden === undefined")


@pytest.mark.parametrize("transform", [
    lambda html: html.replace('id="url"', 'value="private-source-url" id="url"'),
    lambda html: html + '<input type="url">',
    lambda html: html + '<button>Import</button>',
    lambda html: html.replace('<h1>Import a story</h1>', ''),
])
def test_ambiguous_or_nonempty_import_ui_stops_without_changes(page, transform):
    page.set_content(transform(IMPORT))
    with pytest.raises(medium.InspectionStopped) as exc:
        medium.inspect_interface(page)
    assert "private-source-url" not in str(exc.value)
    assert page.evaluate("window.forbidden === undefined")


@pytest.mark.parametrize("href", [
    '/p/import?url=secret', 'https://evil.example/import', '/_/api/posts/import',
    '/new-story', '/p/import#secret', 'javascript:alert(1)', '/m/signout',
])
def test_unsafe_observed_navigation_rejected(page, href):
    page.set_content(f'<a href="{href}">Import a story</a>')
    with pytest.raises(medium.InspectionStopped):
        medium.observed_link(page, medium.IMPORT_NAMES)


def test_ambiguous_links_stop(page):
    page.set_content(STORIES + STORIES)
    with pytest.raises(medium.InspectionStopped):
        medium.observed_link(page, medium.STORIES_NAMES)


def test_import_button_is_not_treated_as_navigation(page):
    page.set_content('<button onclick="window.forbidden=true">Import a story</button>')
    result = medium.MediumResult(Path('/unused'))
    with pytest.raises(medium.InspectionStopped):
        medium.discover_import(page, medium.ReadOnlyGuard(), result)
    assert page.evaluate('window.forbidden === undefined')


@pytest.mark.parametrize("method,url,blocked,stopped", [
    ("GET", medium.HOME, False, False), ("HEAD", medium.HOME, False, False),
    ("OPTIONS", medium.HOME, False, False),
    ("POST", medium.HOME + "_/graphql", True, True),
    ("PUT", medium.HOME + "_/api/posts", True, True),
    ("PATCH", medium.HOME, True, True), ("DELETE", medium.HOME, True, True),
    ("GET", medium.HOME + "_/api/posts/import", True, True),
    ("GET", medium.HOME + "p/import?url=private", True, True),
    ("POST", "https://www.google-analytics.com/g/collect?token=private", True, False),
    ("POST", medium.HOME + "_/api/telemetry", True, True),
    ("POST", "https://unknown.example/private-token", True, True),
])
def test_network_guard_never_reads_bodies_or_headers(method, url, blocked, stopped):
    class Request:
        @property
        def headers(self):
            pytest.fail('Credentials must never be read')
        @property
        def post_data(self):
            pytest.fail('Bodies must never be read')
    request = Request()
    request.method, request.url = method, url
    route = MagicMock(request=request)
    guard = medium.ReadOnlyGuard()
    guard.handle(route)
    assert route.abort.call_count == int(blocked)
    assert route.continue_.call_count == int(not blocked)
    assert guard.stopped == stopped
    assert 'private' not in json.dumps(guard.requests)


def test_guard_stays_closed_after_unexpected_request():
    guard = medium.ReadOnlyGuard()
    for method in ('POST', 'GET'):
        route = MagicMock(request=SimpleNamespace(method=method, url=medium.HOME))
        guard.handle(route)
        route.abort.assert_called_once()
        route.continue_.assert_not_called()


def test_profile_isolation_and_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv('XDG_STATE_HOME')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    paths = runtime_paths(tmp_path / 'project')
    assert medium.medium_profile(tmp_path / 'project') == tmp_path / 'home/.local/state/publish-to-all/browser-profile/medium'
    assert paths.medium_browser_profile != paths.substack_browser_profile


@pytest.mark.parametrize('target_kind', ['substack', 'project', 'nested', 'cookie_alias'])
def test_profile_aliases_cannot_overwrite_substack(tmp_path, target_kind):
    root = tmp_path / 'project'
    root.mkdir()
    paths = runtime_paths(root)
    paths.substack_browser_profile.mkdir(parents=True)
    sentinel = paths.substack_browser_profile / 'Cookies'
    sentinel.write_text('untouched')
    if target_kind == 'cookie_alias':
        paths.medium_browser_profile.mkdir()
        (paths.medium_browser_profile / 'Cookies').symlink_to(sentinel)
    else:
        target = {'substack': paths.substack_browser_profile, 'project': root,
                  'nested': paths.substack_browser_profile / 'nested'}[target_kind]
        paths.medium_browser_profile.symlink_to(target, target_is_directory=True)
    with pytest.raises(ConfigurationError):
        medium.medium_profile(root)
    assert sentinel.read_text() == 'untouched'


@pytest.fixture
def local_medium(browser, monkeypatch):
    """Serve a deterministic current-UI example through the actual context guard."""
    records = []
    pages = {medium.HOME: MENU, medium.ME: MENU,
             medium.HOME + 'me/stories': ACCOUNT + '<a href="/p/import">Import a story</a>',
             medium.HOME + 'p/import': IMPORT}
    contexts = []

    @contextmanager
    def factory(profile, diagnostics, **kwargs):
        context = browser.new_context(service_workers='block')
        contexts.append((profile, kwargs))
        def handle(route):
            records.append((route.request.method, route.request.url))
            def fulfill():
                route.fulfill(status=200, content_type='text/html', body=pages.get(route.request.url, ''))
            if kwargs.get('read_only'):
                kwargs['request_guard'](SimpleNamespace(request=route.request, continue_=fulfill, abort=route.abort))
            else:
                fulfill()
        context.route('**/*', handle)
        yield context
        context.close()
    monkeypatch.setattr(medium, 'persistent_browser', factory)
    monkeypatch.setattr(medium, 'settle', lambda page, guard: guard.check())
    return pages, records, contexts


def test_complete_inspection_no_source_no_draft_no_database(tmp_path, local_medium):
    root = tmp_path / 'project'
    root.mkdir()
    paths = runtime_paths(root)
    paths.database.parent.mkdir(parents=True)
    paths.database.write_bytes(b'production database must not be opened')
    paths.substack_browser_profile.mkdir(parents=True)
    sentinel = paths.substack_browser_profile / 'unchanged'
    sentinel.write_bytes(b'substack state')
    result = medium.run_medium(root, inspect_import=True)
    assert result.authentication == Auth.AUTHENTICATED
    assert result.interface
    assert len(result.navigation) == 4
    assert 'on-input network behavior is unverified' in result.first_write
    assert not result.stopped_reason
    assert local_medium[1] == [('GET', medium.HOME), ('GET', medium.HOME + 'me/stories'), ('GET', medium.HOME + 'p/import')]
    assert paths.database.read_bytes() == b'production database must not be opened'
    assert sentinel.read_bytes() == b'substack state'
    assert local_medium[2][0][0] == paths.medium_browser_profile
    output = format_medium(result, inspect_import=True)
    assert 'Medium source URL entered: No' in output
    assert 'Medium drafts created: 0' in output
    diagnostic = Path(result.diagnostics)
    assert diagnostic.stat().st_mode & 0o777 == 0o600
    assert not diagnostic.is_relative_to(root)


def test_unauthenticated_inspector_never_leaves_home(tmp_path, local_medium):
    local_medium[0][medium.HOME] = '<button>Sign in</button>'
    result = medium.run_medium(tmp_path / 'project', inspect_import=True)
    assert result.authentication == Auth.NOT_AUTHENTICATED
    assert result.interface is None
    assert len(local_medium[1]) == 1


def test_unexpected_mutation_stops_import_navigation(tmp_path, local_medium):
    local_medium[0][medium.HOME] = ACCOUNT + STORIES + '<script>fetch("/_/graphql",{method:"POST"})</script>'
    result = medium.run_medium(tmp_path / 'project', inspect_import=True)
    assert result.authentication == Auth.UNKNOWN
    assert result.interface is None
    assert result.requests[0]['classification'] == 'UNEXPECTED_REQUEST_BLOCKED'
    assert all('me/stories' not in url for _, url in local_medium[1])


def test_sanitized_diagnostics_and_error_messages(tmp_path, page):
    page.goto(medium.HOME + 'secret-login-token?code=private#secret')
    page.set_content('<p>private@example.com secret=private</p><button>Sign in</button>')
    result = medium.MediumResult(tmp_path, current_url=medium.safe_url(page.url))
    result.diagnostics = medium.diagnostics(page, tmp_path, result)
    raw = Path(result.diagnostics).read_text() + format_medium(result)
    assert all(secret not in raw for secret in ('private@example', 'code=', 'secret-login-token', 'secret=private'))
    assert medium.safe_text('email=user@example.com token=private-secret code=123456') == 'email=[email redacted] [secret redacted] [secret redacted]'


@pytest.mark.parametrize('command', ['medium-session', 'medium-import-inspect'])
def test_cli_routes_without_story_config_or_sqlite(tmp_path, monkeypatch, local_medium, capsys, command):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    assert main([command]) == 0
    assert 'AUTHENTICATED' in capsys.readouterr().out
    assert not runtime_paths(root).database.exists()


def test_medium_login_uses_only_medium_profile_and_manual_callback(tmp_path, monkeypatch):
    context = MagicMock()
    page = context.new_page.return_value
    page.url = medium.HOME
    context.pages = [page]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = context
    monkeypatch.setattr(medium, 'persistent_browser', factory)
    monkeypatch.setattr(medium, 'verify', lambda *_: (Auth.AUTHENTICATED, ('Sign out', 'Settings'), False))
    monkeypatch.setattr(medium, 'diagnostics', lambda *_: None)
    callback = MagicMock()
    result = medium.run_medium(tmp_path / 'project', complete_login=callback)
    callback.assert_called_once()
    paths = runtime_paths(tmp_path / 'project')
    assert factory.call_args.args[0] == paths.medium_browser_profile
    assert factory.call_args.args[0] != paths.substack_browser_profile
    assert factory.call_args.kwargs['read_only'] is False
    context.route.assert_called_once()
    for method in ('cookies', 'storage_state', 'add_cookies'):
        getattr(context, method).assert_not_called()
    page.fill.assert_not_called()
    page.type.assert_not_called()
    page.evaluate.assert_not_called()
    assert result.authentication == Auth.AUTHENTICATED
    assert not paths.database.exists()


@pytest.mark.parametrize('failure', ['launch', 'navigation', 'close'])
def test_shared_lifecycle_medium_errors_are_sanitized(tmp_path, monkeypatch, failure):
    driver = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = driver
    monkeypatch.setattr(session, 'sync_playwright', factory)
    context = driver.chromium.launch_persistent_context.return_value
    context.pages = []
    if failure == 'launch':
        driver.chromium.launch_persistent_context.side_effect = PlaywrightError('token=secret')
    elif failure == 'close':
        context.close.side_effect = PlaywrightError('token=secret')
    guard = MagicMock()
    with pytest.raises(BrowserSessionError) as exc:
        with session.persistent_browser(tmp_path / 'medium', tmp_path / 'diagnostics', platform='Medium',
                                        read_only=True, request_guard=guard):
            if failure == 'navigation':
                raise PlaywrightError('token=secret')
    assert 'Medium browser operation failed' in str(exc.value)
    assert 'secret' not in str(exc.value)
    if failure != 'launch':
        context.route.assert_called_once_with('**/*', guard)
        socket = MagicMock()
        context.route_web_socket.call_args.args[1](socket)
        socket.connect_to_server.assert_not_called()
        context.close.assert_called_once()
    assert driver.chromium.launch_persistent_context.call_args.kwargs['service_workers'] == 'block'


def test_late_blocked_request_is_in_diagnostics_and_invalidates_success(tmp_path, page, monkeypatch):
    page.set_content(ACCOUNT)
    guard = medium.ReadOnlyGuard()
    result = medium.MediumResult(tmp_path, authentication=Auth.AUTHENTICATED)
    def screenshot(*args, **kwargs):
        guard.handle(MagicMock(request=SimpleNamespace(method='POST', url=medium.HOME + '_/graphql')))
    monkeypatch.setattr(medium, 'safe_screenshot', screenshot)
    target = medium.diagnostics(page, tmp_path, result, guard)
    data = json.loads(Path(target).read_text())
    assert data['authentication'] == 'UNKNOWN'
    assert data['requests'][0]['path'] == '/_/graphql'
    assert result.stopped_reason


@pytest.mark.parametrize('cancel', [EOFError, KeyboardInterrupt])
def test_medium_login_cli_cancellation_closes_browser(tmp_path, monkeypatch, capsys, cancel):
    import publish_to_all.cli as cli
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    driver = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = driver
    monkeypatch.setattr(session, 'sync_playwright', factory)
    context = driver.chromium.launch_persistent_context.return_value
    monkeypatch.setattr('builtins.input', MagicMock(side_effect=cancel))
    assert cli.main(['medium-login']) == 1
    assert 'Medium session check cancelled' in capsys.readouterr().err
    context.close.assert_called_once()
    assert driver.chromium.launch_persistent_context.call_args.kwargs['user_data_dir'] == str(runtime_paths(root).medium_browser_profile)
    assert not runtime_paths(root).database.exists()


def test_login_and_import_cannot_be_combined(tmp_path):
    with pytest.raises(ValueError):
        medium.run_medium(tmp_path / 'project', complete_login=lambda: None, inspect_import=True)


def test_blocked_endpoint_preserves_safe_identity_without_secrets():
    class Request:
        method = 'POST'
        url = 'https://medium.com/_/some/new/path?token=query-secret#fragment-secret'
        resource_type = 'fetch'

        def __getattr__(self, name):
            pytest.fail(f'Request metadata must not read {name}; bodies/headers are prohibited')

    guard = medium.ReadOnlyGuard()
    route = MagicMock(request=Request())
    guard.handle(route)
    assert guard.requests == [{
        'method': 'POST', 'scheme': 'https', 'hostname': 'medium.com',
        'origin': 'https://medium.com', 'path': '/_/some/new/path',
        'resource_type': 'fetch', 'stage': 'SESSION_CHECK',
        'classification': 'UNEXPECTED_REQUEST_BLOCKED', 'disposition': 'BLOCKED',
    }]
    raw = json.dumps(guard.requests)
    assert all(secret not in raw for secret in ('query-secret', 'fragment-secret', '?', '#'))
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


def test_rendered_signin_heading_is_in_authentication_evidence(page):
    page.set_content('<h1>Welcome back.</h1>')
    state, evidence = medium.authentication(page, medium.ReadOnlyGuard())
    assert state == Auth.NOT_AUTHENTICATED
    assert evidence == ('Welcome back (visible heading)',)


@pytest.mark.parametrize('path,expected', [
    ('/_/some/path', '/_/some/path'),
    ('/cdn-cgi/challenge-platform/h/b/precursor/0123456789abcdef/opaqueIdentifier1234567890/end',
     '/cdn-cgi/challenge-platform/h/[variant]/precursor/[redacted]/[redacted]/[redacted]'),
    ('/_/api/users/01234567-89ab-cdef-0123-456789abcdef/events', '/_/api/users/[redacted]/events'),
    ('/_/api/token/short-value/events', '/_/api/token/[redacted]/events'),
    ('/_/api/token/secret/events', '/_/api/token/[redacted]/events'),
    ('/_/api/users/person%40example.com/events', '/_/api/users/[redacted]/events'),
    ('/_/api/users/a%2540b.co/events', '/_/api/users/[redacted]/events'),
    ('/_/api/token%3Dsecret/events', '/_/api/[redacted]/events'),
    ('/_/api/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwZXJzb24ifQ.signature/events',
     '/_/api/[redacted]/events'),
])
def test_selective_path_redaction_preserves_endpoint_structure(path, expected):
    assert medium.safe_endpoint_path(path) == expected


def test_url_credentials_queries_and_fragments_are_never_endpoint_metadata():
    assert medium.endpoint_identity(
        'https://username:password@medium.com/_/some/path?token=query-secret#fragment-secret'
    ) == ('https', 'medium.com', 'https://medium.com', '/_/some/path')


@pytest.mark.parametrize('url,method,telemetry', [
    ('https://medium.com/cdn-cgi/rum', 'POST', True),
    ('https://medium.com/cdn-cgi/rum?token=private#secret', 'POST', True),
    ('https://medium.com/cdn-cgi/rum', 'PUT', False),
    ('http://medium.com/cdn-cgi/rum', 'POST', False),
    ('https://www.medium.com/cdn-cgi/rum', 'POST', False),
    ('https://medium.com:8443/cdn-cgi/rum', 'POST', False),
    ('https://medium.com.evil.example/cdn-cgi/rum', 'POST', False),
    ('https://user:password@medium.com/cdn-cgi/rum', 'POST', False),
    ('https://medium.com/cdn-cgi/rum/', 'POST', False),
    ('https://medium.com/cdn-cgi/rum/other', 'POST', False),
    ('https://medium.com/cdn-cgi/other', 'POST', False),
    ('https://medium.com/cdn-cgi/challenge-platform/other/0123456789abcdef', 'POST', False),
    ('https://medium.com/_/api/telemetry', 'POST', False),
    ('https://medium.com/_/graphql', 'POST', False),
])
def test_medium_rum_classification_is_exact_and_never_permits_transport(url, method, telemetry):
    route = MagicMock(request=SimpleNamespace(method=method, url=url, resource_type='xhr'))
    guard = medium.ReadOnlyGuard()
    guard.handle(route)
    assert guard.requests[0]['classification'] == (
        'TELEMETRY_BLOCKED' if telemetry else 'UNEXPECTED_REQUEST_BLOCKED')
    assert guard.stopped is not telemetry
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


@pytest.mark.parametrize('html,expected', [
    (ACCOUNT, Auth.AUTHENTICATED),
    ('<a href="/m/signin">Sign in</a>', Auth.NOT_AUTHENTICATED),
    (ACCOUNT + '<button>Sign in</button>', Auth.UNKNOWN),
])
def test_blocked_rum_does_not_override_rendered_authentication(page, html, expected):
    page.set_content(html)
    guard = medium.ReadOnlyGuard()
    guard.handle(MagicMock(request=SimpleNamespace(
        method='POST', url='https://medium.com/cdn-cgi/rum', resource_type='xhr')))
    assert medium.authentication(page, guard)[0] == expected


def test_session_waits_for_controls_after_blocked_telemetry(tmp_path, local_medium, monkeypatch):
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 1)
    local_medium[0][medium.HOME] = '<script>fetch("/cdn-cgi/rum", {method:"POST"}).catch(() => {});</script>'
    local_medium[0][medium.ME] = '''<main></main><script>
        setTimeout(() => {document.querySelector('main').innerHTML = ''' + json.dumps(ACCOUNT) + ''';}, 200);
        </script>'''
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.AUTHENTICATED
    assert set(result.evidence) >= {'Sign out', 'Settings'}
    assert not result.stopped_reason
    assert result.requests[0]['classification'] == 'TELEMETRY_BLOCKED'
    assert result.requests[0]['resource_type'] == 'fetch'
    assert local_medium[1] == [('GET', medium.HOME), ('POST', medium.HOME + 'cdn-cgi/rum'), ('GET', medium.ME)]
    data = json.loads(Path(result.diagnostics).read_text())
    assert data['authentication'] == 'AUTHENTICATED'
    assert 'resource=fetch stage=SESSION_CHECK' in format_medium(result)


def test_session_wait_for_missing_controls_is_bounded(tmp_path, local_medium, monkeypatch):
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 0.2)
    local_medium[0][medium.HOME] = '<main>No account controls</main>'
    local_medium[0][medium.ME] = '<main>No account controls</main>'
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.UNKNOWN
    assert not result.evidence
    assert local_medium[1] == [('GET', medium.HOME), ('GET', medium.ME)]


@pytest.mark.parametrize('endpoint', ['/_/graphql', '/_/api/posts',
    '/cdn-cgi/challenge-platform/other/0123456789abcdef'])
def test_session_unknown_mutation_fails_closed_even_with_account_ui(tmp_path, local_medium, endpoint):
    local_medium[0][medium.HOME] = ACCOUNT + '<script>fetch(' + json.dumps(endpoint) + ',{method:"POST"})</script>'
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.UNKNOWN
    assert result.stopped_reason
    assert result.requests[0]['classification'] == 'UNEXPECTED_REQUEST_BLOCKED'
    assert result.requests[0]['stage'] == 'SESSION_CHECK'
    assert local_medium[1] == [('GET', medium.HOME), ('POST', medium.HOME.rstrip('/') + endpoint)]


def test_session_never_opens_import_or_creates_drafts(tmp_path, local_medium, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Session verification must never invoke import inspection')
    monkeypatch.setattr(medium, 'discover_import', forbidden)
    monkeypatch.setattr(medium, 'inspect_interface', forbidden)
    local_medium[0][medium.HOME] = ACCOUNT + STORIES + '<a href="/p/import">Import a story</a><a href="/new-story">Write</a>'
    local_medium[0][medium.ME] = local_medium[0][medium.HOME]
    root = tmp_path / 'project'
    root.mkdir()
    paths = runtime_paths(root)
    paths.medium_browser_profile.mkdir(parents=True)
    sentinel = paths.medium_browser_profile / 'saved-profile-sentinel'
    sentinel.write_bytes(b'preserve existing profile')
    result = medium.run_medium(root)
    assert result.authentication == Auth.AUTHENTICATED
    assert result.interface is None
    assert result.navigation == ['Medium home', 'GET /me']
    assert local_medium[1] == [('GET', medium.HOME), ('GET', medium.ME)]
    assert local_medium[2][0][0] == paths.medium_browser_profile
    assert sentinel.read_bytes() == b'preserve existing profile'
    assert not paths.database.exists()


@pytest.mark.parametrize('url,method', [
    ('https://medium.com/p/import', 'GET'),
    ('https://medium.com/p/import/', 'GET'),
    ('https://medium.com/p/import?url=private', 'GET'),
    ('https://medium.com/new-story', 'GET'),
    ('https://medium.com/_/api/posts', 'POST'),
    ('https://medium.com/_/api/posts/draft', 'GET'),
])
def test_session_guard_blocks_import_navigation_and_draft_creation(url, method):
    guard = medium.ReadOnlyGuard()
    route = MagicMock(request=SimpleNamespace(method=method, url=url, resource_type='document'))
    guard.handle(route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()
    assert guard.stopped


PRECURSOR_VARIANTS = ('b', 'g', 'synthetic-variant')
PRECURSOR_URL = 'https://medium.com/cdn-cgi/challenge-platform/h/b/precursor/short/opaque/value'


@pytest.mark.parametrize('resource_type', ['xhr', 'fetch'])
@pytest.mark.parametrize('origin', ['https://medium.com', 'https://medium.com:443'])
@pytest.mark.parametrize('variant', PRECURSOR_VARIANTS)
def test_precursor_security_classification_allows_only_safe_metadata(tmp_path, page, resource_type, origin, variant):
    class Request:
        method = 'POST'
        url = (origin + f'/cdn-cgi/challenge-platform/h/{variant}/precursor/'
               'abc/short-secret/opaque?token=query-private#fragment-private')

        def __getattr__(self, name):
            pytest.fail(f'Security verification must never read {name}, including body/headers/cookies/storage')

    request = Request()
    request.resource_type = resource_type
    assert medium.is_precursor(request)
    guard = medium.ReadOnlyGuard()
    route = MagicMock(request=request)
    guard.handle(route)
    route.continue_.assert_called_once_with()
    route.abort.assert_not_called()
    assert not guard.stopped
    assert guard.verification_pending
    assert guard.requests == [{
        'method': 'POST', 'origin': 'https://medium.com',
        'path': '/cdn-cgi/challenge-platform/h/[variant]/precursor/[redacted]/[redacted]/[redacted]',
        'resource_type': resource_type, 'stage': 'SESSION_CHECK',
        'classification': 'EXPECTED SECURITY VERIFICATION', 'disposition': 'ALLOWED',
    }]
    guard.request_finished(request)
    assert not guard.verification_pending
    assert guard.precursor == medium.PrecursorActivity(observed=1, allowed=1, completed=1)
    result = medium.MediumResult(tmp_path, precursor=guard.precursor)
    target = medium.diagnostics(page, tmp_path, result, guard)
    raw = Path(target).read_text() + format_medium(result)
    assert all(value not in raw for value in ('abc', 'short-secret', 'opaque', 'query-private', 'fragment-private'))
    assert f'/h/{variant}/' not in raw
    assert 'EXPECTED SECURITY VERIFICATION — ALLOWED' in format_medium(result)
    assert 'Unexpected requests blocked: 0' in format_medium(result)
    assert 'Precursor requests completed: 1' in format_medium(result)


@pytest.mark.parametrize('url,method,resource_type', [
    ('https://medium.com/cdn-cgi/challenge-platform/other/opaque', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/challenge-platform/h/b/other/opaque', 'POST', 'fetch'),
    ('https://medium.com/cdn-cgi/challenge-platform/h/b/precursor', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/challenge-platform/h/b/precursor-extra/opaque', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/challenge-platform/h//precursor/opaque', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/challenge-platform/h/b/c/d/precursor/opaque', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/challenge-platform/precursor/opaque', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/challenge-platform/h/precursor/b/opaque', 'POST', 'xhr'),
    ('https://medium.com/prefix/cdn-cgi/challenge-platform/h/b/precursor/opaque', 'POST', 'xhr'),
    ('https://medium.com/cdn-cgi/rum', 'POST', 'xhr'),
    ('https://medium.com/api/something', 'POST', 'fetch'),
    ('https://medium.com/graphql', 'POST', 'fetch'),
    ('https://medium.com/_/graphql', 'POST', 'fetch'),
    ('https://medium.com/account/settings', 'POST', 'fetch'),
    ('https://medium.com/arbitrary', 'POST', 'xhr'),
    (PRECURSOR_URL.replace('medium.com', 'other-domain.example'), 'POST', 'xhr'),
    (PRECURSOR_URL.replace('medium.com', 'medium.com.evil.example'), 'POST', 'xhr'),
    (PRECURSOR_URL.replace('medium.com', 'www.medium.com'), 'POST', 'xhr'),
    (PRECURSOR_URL.replace('https:', 'http:'), 'POST', 'xhr'),
    (PRECURSOR_URL.replace('medium.com', 'medium.com:8443'), 'POST', 'xhr'),
    (PRECURSOR_URL.replace('medium.com', 'user:password@medium.com'), 'POST', 'xhr'),
    (PRECURSOR_URL, 'PUT', 'xhr'),
    (PRECURSOR_URL, 'PATCH', 'xhr'),
    (PRECURSOR_URL, 'DELETE', 'xhr'),
    (PRECURSOR_URL, 'POST', 'document'),
    (PRECURSOR_URL, 'POST', 'ping'),
    (PRECURSOR_URL, 'POST', 'other'),
])
@pytest.mark.parametrize('variant', PRECURSOR_VARIANTS)
def test_nearby_requests_cannot_inherit_precursor_transport(url, method, resource_type, variant):
    url = url.replace('/h/b/', f'/h/{variant}/')
    guard = medium.ReadOnlyGuard()
    route = MagicMock(request=SimpleNamespace(method=method, url=url, resource_type=resource_type))
    assert not medium.is_precursor(route.request)
    guard.handle(route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()
    assert guard.precursor == medium.PrecursorActivity()
    assert guard.requests[0]['classification'] != medium.SECURITY_VERIFICATION
    assert guard.requests[0]['disposition'] == 'BLOCKED'
    if url.endswith('/cdn-cgi/rum'):
        assert guard.requests[0]['classification'] == 'TELEMETRY_BLOCKED'
        assert not guard.stopped
    else:
        assert guard.stopped


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'OPTIONS'])
@pytest.mark.parametrize('variant', PRECURSOR_VARIANTS)
def test_precursor_read_methods_use_existing_read_policy_without_security_exception(method, variant):
    guard = medium.ReadOnlyGuard()
    route = MagicMock(request=SimpleNamespace(
        method=method, url=PRECURSOR_URL.replace('/h/b/', f'/h/{variant}/'), resource_type='xhr'))
    assert not medium.is_precursor(route.request)
    guard.handle(route)
    route.continue_.assert_called_once()
    assert not guard.requests
    assert guard.precursor == medium.PrecursorActivity()


def test_precursor_completion_failure_and_repeated_requests_are_counted_without_secrets():
    guard = medium.ReadOnlyGuard()
    requests = [SimpleNamespace(method='POST', url=PRECURSOR_URL, resource_type='xhr') for _ in range(3)]
    for request in requests:
        guard.handle(MagicMock(request=request))
    assert guard.precursor.allowed == 3
    assert len(guard.requests) == 1  # Sanitized metadata may coalesce; counts must not.
    guard.request_finished(requests[0])
    guard.request_finished(requests[0])
    guard.request_failed(requests[1])
    guard.request_failed(requests[1])
    guard.request_finished(SimpleNamespace())
    assert guard.precursor == medium.PrecursorActivity(observed=3, allowed=3, completed=1, failed=1)
    assert guard.verification_pending
    guard.request_finished(requests[2])
    assert not guard.verification_pending


@pytest.mark.parametrize('html,expected', [
    ('', Auth.UNKNOWN), (STORIES, Auth.UNKNOWN),
    (ACCOUNT, Auth.AUTHENTICATED), ('<button>Sign in</button>', Auth.NOT_AUTHENTICATED),
    ('<button>Get started</button>', Auth.NOT_AUTHENTICATED),
    (ACCOUNT + '<button>Sign in</button>', Auth.UNKNOWN),
])
@pytest.mark.parametrize('variant', PRECURSOR_VARIANTS)
def test_allowed_and_completed_precursor_is_not_authentication(page, html, expected, variant):
    guard = medium.ReadOnlyGuard()
    request = SimpleNamespace(
        method='POST', url=PRECURSOR_URL.replace('/h/b/', f'/h/{variant}/'), resource_type='xhr')
    guard.handle(MagicMock(request=request))
    page.set_content(html)
    assert medium.authentication(page, guard)[0] == expected
    guard.request_finished(request)
    assert medium.authentication(page, guard)[0] == expected


@pytest.mark.parametrize('endpoint,method', [
    ('/_/graphql', 'POST'), ('/graphql', 'POST'), ('/api/user', 'POST'),
    ('/_/api/posts', 'POST'), ('/_/api/posts/import', 'POST'),
    ('/_/api/posts/publish', 'POST'), ('/_/api/posts/draft', 'GET'),
    ('/p/import', 'GET'), ('/new-story', 'GET'), ('/arbitrary', 'POST'),
])
@pytest.mark.parametrize('variant', PRECURSOR_VARIANTS)
def test_allowed_precursor_does_not_open_mutation_guard(page, endpoint, method, variant):
    guard = medium.ReadOnlyGuard()
    verification = SimpleNamespace(
        method='POST', url=PRECURSOR_URL.replace('/h/b/', f'/h/{variant}/'), resource_type='xhr')
    guard.handle(MagicMock(request=verification))
    guard.request_finished(verification)
    route = MagicMock(request=SimpleNamespace(method=method, url=medium.HOME.rstrip('/') + endpoint, resource_type='fetch'))
    guard.handle(route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()
    page.set_content(ACCOUNT)
    assert medium.authentication(page, guard)[0] == Auth.UNKNOWN
    subsequent = MagicMock(request=verification)
    guard.handle(subsequent)
    subsequent.abort.assert_called_once()
    subsequent.continue_.assert_not_called()
    assert guard.requests[-1]['classification'] == medium.SECURITY_VERIFICATION
    assert guard.requests[-1]['disposition'] == 'BLOCKED'
    assert guard.precursor.allowed == 1


def test_rate_limit_after_precursor_stops_even_with_account_controls(page):
    guard = medium.ReadOnlyGuard()
    request = SimpleNamespace(method='POST', url=PRECURSOR_URL, resource_type='xhr')
    guard.handle(MagicMock(request=request))
    guard.response(SimpleNamespace(status=429, url=PRECURSOR_URL))
    guard.request_finished(request)
    page.set_content(ACCOUNT)
    assert medium.authentication(page, guard)[0] == Auth.RATE_LIMITED
    route = MagicMock(request=request)
    guard.handle(route)
    route.abort.assert_called_once()
    assert guard.precursor.allowed == 1


@pytest.mark.parametrize('html,expected', [
    (ACCOUNT + STORIES + '<a href="/p/import">Import a story</a><a href="/new-story">Write</a>', Auth.AUTHENTICATED),
    ('<button>Sign in</button><button>Get started</button>', Auth.NOT_AUTHENTICATED),
    ('<main>No account controls</main>', Auth.UNKNOWN),
    (ACCOUNT + '<button>Sign in</button>', Auth.UNKNOWN),
])
@pytest.mark.parametrize('variant', PRECURSOR_VARIANTS)
def test_session_precursor_completes_before_ui_without_import_draft_or_sqlite(
        tmp_path, local_medium, monkeypatch, html, expected, variant):
    import sqlite3

    def forbidden(*args, **kwargs):
        pytest.fail('Session verification must not enter Import, create a draft, or open SQLite')

    monkeypatch.setattr(medium, 'discover_import', forbidden)
    monkeypatch.setattr(medium, 'inspect_interface', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 0.7)
    monkeypatch.setattr(medium, 'VERIFICATION_RENDER_SECONDS', 0.1)
    precursor_url = PRECURSOR_URL.replace('/h/b/', f'/h/{variant}/')
    local_medium[0][medium.HOME] = '''<main></main><script>
        fetch('/cdn-cgi/rum', {method:'POST'}).catch(() => {});
        fetch(''' + json.dumps(precursor_url) + ''', {method:'POST'}).then(response => response.text()).then(() => {
            setTimeout(() => {document.querySelector('main').innerHTML = ''' + json.dumps(html) + ''';}, 100);
        });</script>'''
    local_medium[0][medium.ME] = html
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == expected
    assert not result.stopped_reason
    assert result.interface is None
    assert result.precursor == medium.PrecursorActivity(observed=1, allowed=1, completed=1)
    assert local_medium[1] == [
        ('GET', medium.HOME), ('POST', medium.HOME + 'cdn-cgi/rum'), ('POST', precursor_url), ('GET', medium.ME)]
    assert result.navigation == ['Medium home', 'GET /me']
    data = json.loads(Path(result.diagnostics).read_text())
    assert data['precursor'] == {'observed': 1, 'allowed': 1, 'completed': 1, 'failed': 0}
    assert data['authentication'] == expected.value
    assert {r['classification']: r['disposition'] for r in data['requests']} == {
        'TELEMETRY_BLOCKED': 'BLOCKED', 'EXPECTED SECURITY VERIFICATION': 'ALLOWED'}
    output = format_medium(result)
    assert 'EXPECTED NON-MUTATING TELEMETRY — BLOCKED' in output
    assert 'Medium drafts created: 0' in output


def test_session_pending_verification_wait_is_bounded(page, monkeypatch):
    guard = medium.ReadOnlyGuard()
    request = SimpleNamespace(method='POST', url=PRECURSOR_URL, resource_type='xhr')
    guard.handle(MagicMock(request=request))
    page.set_content(ACCOUNT)
    monkeypatch.setattr(medium, 'navigate', lambda *_: None)
    ticks = iter([0.0, 0.1, 1.0])
    monkeypatch.setattr(medium, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 0.5)
    state, evidence, opened = medium.verify(page, guard)
    assert state == Auth.AUTHENTICATED  # Actual UI, not pending verification, is decisive.
    assert 'Sign out' in evidence
    assert guard.precursor.completed == 0
    assert not opened


def test_session_allows_rendering_after_verification_before_using_signin_ui(tmp_path, local_medium, monkeypatch):
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 1)
    monkeypatch.setattr(medium, 'VERIFICATION_RENDER_SECONDS', 0.4)
    local_medium[0][medium.HOME] = '''<main><button>Sign in</button></main><script>
        fetch(''' + json.dumps(PRECURSOR_URL) + ''', {method:'POST'}).then(response => response.text()).then(() => {
            setTimeout(() => {document.querySelector('main').innerHTML = ''' + json.dumps(ACCOUNT) + ''';}, 200);
        });</script>'''
    local_medium[0][medium.ME] = ACCOUNT
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.AUTHENTICATED
    assert set(result.evidence) == {'Sign out', 'Settings'}
    assert result.precursor.completed == 1
    assert local_medium[1] == [('GET', medium.HOME), ('POST', PRECURSOR_URL), ('GET', medium.ME)]


@pytest.fixture
def account_server(browser, monkeypatch):
    """Real redirects on loopback; non-loopback transport is never permitted."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlsplit

    pages = {'/': (200, ''), '/me': (302, '/@test.owner'),
             '/@test.owner': (200, '<title>Fixture profile – Medium</title><button>Edit profile</button>')}
    records = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            records.append(('GET', self.path))
            status, body = pages.get(urlsplit(self.path).path, (404, ''))
            self.send_response(status)
            if 300 <= status < 400:
                self.send_header('Location', body)
                body = ''
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            records.append(('POST', self.path))
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    original_trusted = medium.trusted
    monkeypatch.setattr(medium, 'HOME', origin + '/')
    monkeypatch.setattr(medium, 'ME', origin + '/me')
    monkeypatch.setattr(medium, 'trusted', lambda url: url.startswith(origin + '/') or original_trusted(url))
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 0.4)
    monkeypatch.setattr(medium, 'VERIFICATION_RENDER_SECONDS', 0.05)
    monkeypatch.setattr(medium, 'settle', lambda page, guard: (page.wait_for_timeout(100), guard.check()))

    @contextmanager
    def factory(profile, diagnostics, **kwargs):
        context = browser.new_context(service_workers='block')

        def route_request(route):
            # Medium security/telemetry fixtures are fulfilled locally too.
            def transport():
                if route.request.url.startswith(origin + '/'):
                    route.continue_()
                else:
                    route.fulfill(status=200, content_type='text/plain', body='verified',
                                  headers={'Access-Control-Allow-Origin': '*'})
            kwargs['request_guard'](SimpleNamespace(request=route.request, continue_=transport, abort=route.abort))

        context.route('**/*', route_request)
        try:
            yield context
        finally:
            context.close()

    monkeypatch.setattr(medium, 'persistent_browser', factory)
    try:
        yield pages, records
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('destination,html,expected', [
    ('/@test.owner', '<button>Edit profile</button>', Auth.AUTHENTICATED),
    ('/@test.owner', '<h1>A public profile</h1>', Auth.UNKNOWN),
    ('/@test.owner', '<button>Settings</button>', Auth.UNKNOWN),
    ('/@test.owner', '<a href="/new-story">Write</a>', Auth.UNKNOWN),
    ('/@test.owner', '<button hidden>Edit profile</button>', Auth.UNKNOWN),
    ('/@test.owner', '<button>Sign in</button>', Auth.UNKNOWN),
    ('/@test.owner', '<button>Edit profile</button><button>Get started</button>', Auth.UNKNOWN),
    ('/m/signin', '<h1>Welcome back.</h1><button>Sign in</button>', Auth.NOT_AUTHENTICATED),
    ('/m/signup', '<button>Sign up</button>', Auth.NOT_AUTHENTICATED),
    ('/m/signin', '', Auth.UNKNOWN),
    ('/m/signin', ACCOUNT, Auth.UNKNOWN),
    ('/me', '<button>Get started</button>', Auth.NOT_AUTHENTICATED),
    ('/me', ACCOUNT, Auth.AUTHENTICATED),
    ('/me', '<button>Edit profile</button>', Auth.UNKNOWN),
])
def test_me_navigation_requires_rendered_ownership(tmp_path, account_server, destination, html, expected):
    pages, records = account_server
    pages['/me'] = (302, destination) if destination != '/me' else (200, html)
    pages[destination] = (200, html)
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == expected
    assert result.account_probe.requested_url == medium.ME
    assert result.account_probe.final_url == medium.safe_url(medium.HOME.rstrip('/') + destination)
    assert result.account_probe.completed
    assert result.account_probe.redirect_chain == ([{
        'url': medium.safe_url(medium.ME), 'method': 'GET', 'status': 302,
    }] if destination != '/me' else []) + [{
        'url': medium.safe_url(medium.HOME.rstrip('/') + destination), 'method': 'GET', 'status': 200,
    }]
    assert records == [('GET', '/'), ('GET', '/me')] + ([('GET', destination)] if destination != '/me' else [])


@pytest.mark.parametrize('destination', ['/p/import', '/p/import/', '/p/import?url=secret-source',
                                         '/new-story', '/_/api/posts/draft', '/m/signout'])
def test_me_http_redirect_cannot_reach_import_or_create_draft(tmp_path, account_server, destination):
    pages, records = account_server
    pages['/me'] = (302, destination)
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.UNKNOWN
    assert result.stopped_reason
    assert records == [('GET', '/'), ('GET', '/me')]
    assert result.requests[-1]['classification'] == 'UNEXPECTED_REQUEST_BLOCKED'
    assert result.requests[-1]['disposition'] == 'BLOCKED'
    assert not result.account_probe.completed


@pytest.mark.parametrize('endpoint,method', [('/_/graphql', 'POST'), ('/_/api/posts', 'POST'),
    ('/account/settings', 'PATCH'), ('/arbitrary', 'POST'), ('/_/api/posts/import', 'POST'), ('/me', 'POST')])
def test_me_unknown_mutation_stops_despite_owner_ui(tmp_path, account_server, endpoint, method):
    pages, records = account_server
    pages['/@test.owner'] = (200, '<button>Edit profile</button><script>fetch(' + json.dumps(endpoint)
                              + ',{method:' + json.dumps(method) + '}).catch(()=>{});</script>')
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.UNKNOWN
    assert result.stopped_reason
    assert ('POST', endpoint) not in records
    assert result.requests[-1]['path'] == endpoint
    assert result.requests[-1]['disposition'] == 'BLOCKED'


def test_me_cookies_and_final_url_alone_are_insufficient(page):
    page.goto('https://medium.com/@test.owner')
    page.context.add_cookies([{'name': 'session', 'value': 'cookie-private', 'url': medium.HOME}])
    probe = medium.AccountProbe(requested_url=medium.ME, completed=True,
        navigation=[medium.safe_url(page.url)],
        redirect_chain=[{'url': medium.safe_url(page.url), 'method': 'GET', 'status': 200}])
    assert medium.authentication(page, medium.ReadOnlyGuard(), probe)[0] == Auth.UNKNOWN
    page.set_content('<button>Edit profile</button>')
    probe.completed = False
    assert medium.authentication(page, medium.ReadOnlyGuard(), probe)[0] == Auth.UNKNOWN
    probe.completed = True
    probe.ambiguous = True
    assert medium.authentication(page, medium.ReadOnlyGuard(), probe)[0] == Auth.UNKNOWN


def test_me_security_verification_completes_and_rum_stays_blocked(tmp_path, account_server):
    pages, records = account_server
    pages['/'] = (200, '<script>fetch(' + json.dumps(PRECURSOR_URL) + ',{method:"POST"}).then(r=>r.text());</script>')
    pages['/@test.owner'] = (200, '''<main></main><script>
        fetch('https://medium.com/cdn-cgi/rum',{method:'POST'}).catch(()=>{});
        fetch(''' + json.dumps(PRECURSOR_URL) + ''',{method:'POST'}).then(r=>r.text()).then(()=>{
            document.querySelector('main').innerHTML='<button>Edit profile</button>';
        });</script>''')
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.AUTHENTICATED
    assert result.precursor == medium.PrecursorActivity(observed=2, allowed=2, completed=2)
    assert {r['classification']: r['disposition'] for r in result.requests} == {
        'EXPECTED SECURITY VERIFICATION': 'ALLOWED', 'TELEMETRY_BLOCKED': 'BLOCKED'}
    assert all(method == 'GET' for method, _ in records)


@pytest.mark.parametrize('failed', [False, True])
def test_me_not_requested_until_home_verification_completes(page, monkeypatch, failed):
    guard = medium.ReadOnlyGuard()
    request = SimpleNamespace(method='POST', url=PRECURSOR_URL, resource_type='fetch')
    guard.handle(MagicMock(request=request))
    if failed:
        guard.request_failed(request)
    navigate = MagicMock()
    monkeypatch.setattr(medium, 'navigate', navigate)
    monkeypatch.setattr(medium, 'guard_navigation_redirects', lambda *_: None)
    monkeypatch.setattr(medium, 'AUTHENTICATION_WAIT_SECONDS', 0.1)
    probe = medium.AccountProbe()
    with pytest.raises(medium.InspectionStopped, match='Security verification'):
        medium.verify_account_probe(page, guard, probe)
    navigate.assert_called_once_with(page, medium.HOME, guard)
    assert probe.requested_url == '[not requested]'


def test_me_diagnostics_remove_navigation_secrets(tmp_path, account_server):
    pages, _ = account_server
    pages['/me'] = (302, '/@test.owner?token=query-private#fragment-private')
    pages['/@test.owner'] = (200, '<title>user@example.com token=title-private – Medium</title>'
        '<button>Edit profile</button><script>fetch("https://medium.com/cdn-cgi/rum?token=request-private",'
        '{method:"POST",headers:{"X-Fixture":"header-private"},body:"body-private"}).catch(()=>{});</script>')
    result = medium.run_medium(tmp_path / 'project')
    assert result.authentication == Auth.AUTHENTICATED
    raw = Path(result.diagnostics).read_text() + format_medium(result)
    assert all(secret not in raw for secret in ('test.owner', 'query-private', 'fragment-private',
        'user@example.com', 'title-private', 'request-private', 'header-private', 'body-private'))
    assert result.account_probe.page_title == '[email redacted] [secret redacted] – Medium'
    assert result.account_probe.owner_controls == ('Edit profile',)
    assert result.account_probe.signed_out_controls == ()


def test_me_reports_late_conflict_as_unknown(tmp_path, page, monkeypatch):
    page.goto('https://medium.com/@test.owner')
    page.set_content('<button>Edit profile</button>')
    probe = medium.AccountProbe(requested_url=medium.ME, completed=True, navigation=[medium.safe_url(page.url)],
        redirect_chain=[{'url': medium.safe_url(page.url), 'method': 'GET', 'status': 200}])
    result = medium.MediumResult(tmp_path, authentication=Auth.AUTHENTICATED, account_probe=probe)
    monkeypatch.setattr(medium, 'safe_screenshot',
                        lambda *_args, **_kwargs: page.set_content('<button>Edit profile</button><button>Sign in</button>'))
    medium.diagnostics(page, tmp_path, result, medium.ReadOnlyGuard())
    assert result.authentication == Auth.UNKNOWN
    assert result.account_probe.signed_out_controls == ('Sign in',)


@pytest.mark.parametrize('key', ['token', 'access_token', 'refresh-token', 'session', 'session_id', 'authorization'])
def test_me_title_redacts_secret_labels(key):
    assert medium.safe_text(f'Medium {key}=short-private') == 'Medium [secret redacted]'
