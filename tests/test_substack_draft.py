from contextlib import contextmanager
from unittest.mock import MagicMock
import sqlite3

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import editor
from publish_to_all.browser.substack import AuthenticationState as Auth
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, PublishToAllError
from publish_to_all.publishers.substack import SubstackPublisher
from publish_to_all.state import (
    DuplicatePublicationError, MIGRATIONS, PublicationRepository, PublicationStatus as Status,
)
from publish_to_all.story import load_story

URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'In').mkdir()
    (root / 'In/source.md').write_text('---\ntitle: "Exact — title & punctuation!"\nsubtitle: Never insert\ntags: [private]\n---\nNever insert body.')
    Image.new('RGB', (2, 2)).save(root / 'In/source.png')
    (root / 'config.toml').write_text(f'[substack]\npublication_url = "{URL}"')
    runtime_paths(root).substack_browser_profile.mkdir(parents=True)
    return root


@pytest.fixture
def browser(monkeypatch):
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page
    @contextmanager
    def launch(*args, **kwargs):
        assert kwargs == {'headless': False}
        yield context
    launcher = MagicMock(side_effect=launch)
    monkeypatch.setattr(application, 'persistent_browser', launcher)
    monkeypatch.setattr(application, 'verify_page', MagicMock(return_value=Auth.AUTHENTICATED))
    return launcher, context, page


@pytest.fixture
def actions(monkeypatch):
    mocks = {}
    for name in ('navigate_dashboard', 'open_new_post', 'locate_title_field', 'enter_title', 'confirm_draft', 'save_visible'):
        mocks[name] = MagicMock()
        monkeypatch.setattr(editor, name, mocks[name])
    mocks['save_visible'].return_value = False
    mocks['confirm_draft'].return_value = DRAFT
    return mocks


def repository(project):
    return PublicationRepository(runtime_paths(project).database)


def record(project):
    return repository(project).get_publication(load_story(project / 'In').source_hash, 'substack')


@pytest.mark.parametrize('failure', ['config', 'missing_config', 'story', 'title', 'image', 'session'])
def test_local_preflight_never_opens_browser(project, browser, actions, capsys, failure):
    if failure == 'config':
        (project / 'config.toml').write_text('[broken')
    elif failure == 'missing_config':
        (project / 'config.toml').unlink()
    elif failure in ('story', 'title'):
        (project / 'In/source.md').write_text('---\ntitle: []\n---\nBody' if failure == 'title' else 'invalid')
    elif failure == 'image':
        (project / 'In/source.png').write_bytes(b'bad image')
    else:
        runtime_paths(project).substack_browser_profile.rmdir()
    assert main(['substack']) == 1
    browser[0].assert_not_called()
    actions['open_new_post'].assert_not_called()
    assert 'Traceback' not in capsys.readouterr().err


@pytest.mark.parametrize('auth', [Auth.UNKNOWN, Auth.NOT_AUTHENTICATED])
def test_authentication_precedes_attempt(project, browser, actions, monkeypatch, capsys, auth):
    monkeypatch.setattr(application, 'verify_page', lambda *_: auth)
    assert main(['substack']) == 1
    assert record(project) is None
    actions['open_new_post'].assert_not_called()
    assert 'substack-login' in capsys.readouterr().err


def test_rate_limit_stops_before_attempt_and_leaves_sqlite_unchanged(
        project, browser, actions, monkeypatch, capsys):
    repo = repository(project)
    database = runtime_paths(project).database
    before = database.read_bytes()
    check = MagicMock(return_value=Auth.RATE_LIMITED)
    monkeypatch.setattr(application, 'verify_page', check)

    assert main(['substack']) == 1

    assert database.read_bytes() == before
    assert repo.get_publication(load_story(project / 'In').source_hash, 'substack') is None
    check.assert_called_once_with(browser[2], URL, 10, True)
    actions['navigate_dashboard'].assert_not_called()
    actions['open_new_post'].assert_not_called()
    output = capsys.readouterr().err
    for text in ('temporarily rate limited', 'Rate limiting detected: Yes',
                 'Retry manually later', 'No local publication state was changed',
                 'No draft was created', 'Nothing was published'):
        assert text in output


def test_first_run_rate_limit_does_not_create_sqlite(
        project, browser, actions, monkeypatch, capsys):
    database = runtime_paths(project).database
    assert not database.exists()
    monkeypatch.setattr(application, 'verify_page', lambda *_: Auth.RATE_LIMITED)
    assert main(['substack']) == 1
    assert not database.exists()
    actions['open_new_post'].assert_not_called()
    assert 'Retry manually later' in capsys.readouterr().err


@pytest.mark.parametrize('status', ['draft', 'published', 'in_progress', 'uncertain'])
def test_duplicate_precedes_browser(project, browser, actions, capsys, status):
    repo = repository(project)
    attempt = repo.begin_attempt(load_story(project / 'In'), 'substack')
    if status in ('draft', 'published'):
        repo.mark_draft_created(attempt.id, DRAFT)
    if status == 'published':
        repo.mark_published(attempt.id)
    if status == 'uncertain':
        repo.mark_failed(attempt.id, 'Uncertain', needs_reconciliation=True)
    assert main(['substack']) == 1
    output = capsys.readouterr().err
    assert 'exact version' in output and 'No new draft was created' in output
    if status in ('draft', 'published'):
        assert DRAFT in output
    browser[0].assert_not_called()


@pytest.mark.parametrize('url', [DRAFT, None])
def test_success_title_only_and_timestamps(project, browser, actions, capsys, url):
    before = {p: p.read_bytes() for p in (project / 'In').iterdir()}
    def opening(*_):
        assert record(project).status == Status.DRAFT_CREATING
    actions['open_new_post'].side_effect = opening
    actions['confirm_draft'].return_value = url
    assert main(['substack']) == 0
    saved = record(project)
    assert saved.status == Status.DRAFT_CREATED and saved.draft_url == url
    assert saved.updated_at >= saved.last_attempt_at == saved.created_at
    assert not saved.needs_reconciliation
    actions['enter_title'].assert_called_once_with(browser[2], actions['locate_title_field'].return_value,
                                                   'Exact — title & punctuation!', URL)
    output = capsys.readouterr().out
    assert 'Added:\nTitle' in output and 'Not added yet:\nStory body\nImage' in output
    assert (url or 'Not available') in output and 'Nothing was published.' in output
    assert before == {p: p.read_bytes() for p in (project / 'In').iterdir()}
    browser[2].fill.assert_not_called()
    browser[2].set_input_files.assert_not_called()


@pytest.mark.parametrize('action,step', [
    ('navigate_dashboard', 'Navigate to dashboard'), ('open_new_post', 'Open new-post editor'),
    ('locate_title_field', 'Locate title field'), ('enter_title', 'Enter title'),
    ('confirm_draft', 'Confirm draft save'),
])
def test_failure_reports_safe_step_and_blocks_uncertain_retry(project, browser, actions, capsys, action, step):
    actions[action].side_effect = RuntimeError('password=secret token=secret private body')
    assert main(['substack']) == 1
    saved = record(project)
    output = capsys.readouterr().err
    assert saved.status == Status.FAILED
    assert step in output and step in saved.error_message
    assert 'secret' not in output + saved.error_message
    assert saved.needs_reconciliation == (action != 'navigate_dashboard')
    screenshot = next(runtime_paths(project).diagnostics.glob('*.png'))
    assert str(screenshot) in output and str(screenshot) in saved.error_message
    assert 'Nothing was published.' in output
    if action == 'locate_title_field':
        actions['enter_title'].assert_not_called()
    if action != 'confirm_draft':
        actions['confirm_draft'].assert_not_called()


def test_cancel_and_capture_failure(project, browser, actions, capsys):
    from playwright.sync_api import Error
    browser[2].screenshot.side_effect = Error('token=secret')
    actions['enter_title'].side_effect = KeyboardInterrupt()
    assert main(['substack']) == 1
    assert record(project).status == Status.FAILED
    assert record(project).needs_reconciliation
    assert 'Unavailable' in capsys.readouterr().err


def test_failure_without_url_still_blocks_retry(project, browser, actions, capsys):
    browser[2].url = URL + '/publish/home'
    actions['open_new_post'].side_effect = RuntimeError('unknown result')
    assert main(['substack']) == 1
    assert record(project).draft_url is None
    browser[0].reset_mock()
    assert main(['substack']) == 1
    browser[0].assert_not_called()


def test_no_publish_implementation(project, browser):
    publisher = SubstackPublisher(repository(project), browser[2], URL, runtime_paths(project).diagnostics)
    with pytest.raises(PublishToAllError, match='not implemented'):
        publisher.publish(None, None)
    assert not browser[2].mock_calls


@pytest.mark.parametrize('label', ['Publish', 'PUBLISH NOW', 'Send to everyone', 'Schedule...', 'Continue', 'Continue to publish'])
def test_dangerous_controls_never_clicked(label):
    page = MagicMock(url=URL + '/publish/home')
    control = MagicMock()
    control.inner_text.return_value = label
    control.get_attribute.return_value = 'Create'
    assert editor.is_publishing_control(label)
    with pytest.raises(BrowserSessionError):
        editor.click_creation_control(page, control, URL, editor.CREATE)
    control.click.assert_not_called()


@pytest.mark.parametrize('url,expected', [
    (DRAFT, DRAFT), (DRAFT + '?token=secret#private', DRAFT),
    (URL + '/publish/post/new', None), (URL + '/publish/home', None),
    ('https://other.substack.com/publish/post/123', None),
    ('https://example.substack.com.evil.org/publish/post/123', None),
    ('https://secret@example.substack.com/publish/post/123', None),
])
def test_url_is_observed_and_sanitized(url, expected):
    assert editor.extract_draft_url(MagicMock(url=url), URL) == expected


def test_migrate_v1_preserves_records(tmp_path):
    path = tmp_path / 'v1.sqlite3'
    with sqlite3.connect(path) as connection:
        for sql in MIGRATIONS[0]:
            connection.execute(sql)
        connection.execute('PRAGMA user_version=1')
        connection.execute("INSERT INTO stories VALUES (1, 'x.md', 'Title', 'hash', 'then', 'then')")
        connection.execute("INSERT INTO publications VALUES (1, 1, 'substack', 'draft_created', NULL, NULL, 'then', 'then', 'then', NULL)")
    repo = PublicationRepository(path)
    saved = repo.find_duplicate('hash', 'substack')
    assert saved.status == Status.DRAFT_CREATED and not saved.needs_reconciliation
    assert saved.created_at == 'then'


def test_substack_help(capsys):
    with pytest.raises(SystemExit) as result:
        main(['substack', '--help'])
    assert result.value.code == 0
    assert 'substack' in capsys.readouterr().out


def title_field(value=''):
    field = MagicMock()
    field.get_attribute.return_value = None
    field.input_value.return_value = value
    return field


def test_enter_title_preserves_exact_value_and_only_fills_title():
    page = MagicMock(url=DRAFT)
    field = title_field()
    title = '  A title — with Unicode & spaces  '
    field.fill.side_effect = lambda value: setattr(field.input_value, 'return_value', value)
    editor.enter_title(page, field, title, URL)
    field.fill.assert_called_once_with(title)
    field.blur.assert_called_once()
    assert not page.mock_calls


@pytest.mark.parametrize('url,value', [(DRAFT, 'Existing title'), ('https://other.substack.com/publish/post/123', '')])
def test_enter_title_refuses_existing_or_wrong_publication(url, value):
    field = title_field(value)
    with pytest.raises(BrowserSessionError):
        editor.enter_title(MagicMock(url=url), field, 'New title', URL)
    field.fill.assert_not_called()


def test_ambiguous_field_does_not_guess():
    locator = MagicMock()
    locator.all.return_value = [MagicMock(), MagicMock()]
    with pytest.raises(BrowserSessionError, match='Ambiguous'):
        editor.unique_visible(locator)


def test_confirm_never_accepts_url_or_stale_saved_label(monkeypatch):
    page = MagicMock(url=DRAFT)
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    page.get_by_text.return_value.all.return_value = []
    verification = page.context.new_page.return_value
    verification.url = DRAFT
    missing = MagicMock()
    missing.all.return_value = []
    monkeypatch.setattr(editor, 'title_fields', lambda _: missing)
    with pytest.raises(BrowserSessionError, match='confirm'):
        editor.confirm_draft(page, title_field('Title'), 'Title', URL, previously_saved=True, timeout=0)
    verification.close.assert_called_once()


def test_confirm_fresh_autosave_without_stable_url(monkeypatch):
    page = MagicMock(url=URL + '/publish/post/new')
    monkeypatch.setattr(editor, 'save_visible', MagicMock(side_effect=[False, True]))
    page.get_by_text.return_value.all.return_value = []
    assert editor.confirm_draft(page, title_field('Title'), 'Title', URL, previously_saved=True) is None
    page.context.new_page.assert_not_called()


def test_confirm_reopens_known_url_and_checks_exact_title(monkeypatch):
    page = MagicMock(url=DRAFT)
    verification = page.context.new_page.return_value
    verification.url = DRAFT
    page.get_by_text.return_value.all.return_value = []
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    fields = MagicMock()
    fields.all.return_value = [title_field('Title')]
    monkeypatch.setattr(editor, 'title_fields', lambda _: fields)
    assert editor.confirm_draft(page, title_field('Title'), 'Title', URL, previously_saved=True) == DRAFT
    verification.goto.assert_called_once_with(DRAFT, wait_until='domcontentloaded')
    verification.close.assert_called_once()
    page.reload.assert_not_called()


def test_title_mismatch_never_confirms(monkeypatch):
    page = MagicMock(url=URL + '/publish/post/new')
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    page.get_by_text.return_value.all.return_value = []
    with pytest.raises(BrowserSessionError):
        editor.confirm_draft(page, title_field('Other'), 'Title', URL, timeout=0)


def test_atomic_duplicate_recheck_after_authentication(project, browser, actions, monkeypatch, capsys):
    def concurrent_attempt(*_):
        repository(project).begin_attempt(load_story(project / 'In'), 'substack')
        return Auth.AUTHENTICATED
    monkeypatch.setattr(application, 'verify_page', concurrent_attempt)
    assert main(['substack']) == 1
    actions['open_new_post'].assert_not_called()
    assert 'No new draft was created' in capsys.readouterr().err


def test_database_commit_failure_is_not_reported_as_success(project, browser, actions, monkeypatch, capsys):
    from publish_to_all.state import StateError
    def failed_commit(*_):
        raise StateError('private error')
    monkeypatch.setattr(PublicationRepository, 'mark_draft_created', failed_commit)
    assert main(['substack']) == 1
    saved = record(project)
    assert saved.status == Status.FAILED and saved.draft_url == DRAFT
    assert saved.needs_reconciliation
    output = capsys.readouterr().err
    assert 'Record draft in SQLite' in output and 'private error' not in output


@pytest.mark.parametrize('direct', [True, False])
def test_new_post_clicks_only_recognized_creation_controls(monkeypatch, direct):
    page = MagicMock(url=URL + '/publish/home')
    create = MagicMock()
    create.inner_text.return_value = 'New post' if direct else 'Create'
    create.get_attribute.return_value = None
    choice = MagicMock()
    choice.inner_text.return_value = 'Article'
    choice.get_attribute.return_value = None
    monkeypatch.setattr(editor, 'wait_visible', MagicMock(side_effect=[create, choice]))
    monkeypatch.setattr(editor, 'unique_visible', lambda _: choice if direct else None)
    editor.open_new_post(page, URL)
    create.click.assert_called_once()
    assert choice.click.call_count == (0 if direct else 1)
    page.fill.assert_not_called()
    page.click.assert_not_called()


def test_creation_link_to_other_publication_is_refused():
    page = MagicMock(url=URL + '/publish/home')
    control = MagicMock()
    control.inner_text.return_value = 'New post'
    control.get_attribute.side_effect = lambda attr: 'https://other.substack.com/publish/post/new' if attr == 'href' else None
    with pytest.raises(BrowserSessionError, match='outside'):
        editor.click_creation_control(page, control, URL, editor.CREATE)
    control.click.assert_not_called()


def test_reconciling_unknown_failure_clears_block_flag(project):
    repo = repository(project)
    story = load_story(project / 'In')
    attempt = repo.begin_attempt(story, 'substack')
    repo.mark_failed(attempt.id, 'Unknown outcome', needs_reconciliation=True)
    recovered = repo.mark_draft_created(attempt.id, DRAFT)
    assert not recovered.needs_reconciliation
    assert repo.find_duplicate(story.source_hash, 'substack').id == recovered.id


@pytest.mark.parametrize('matches,known,link,linked', [
    ((DRAFT,), DRAFT, False, False), ((DRAFT,), DRAFT, True, True),
    ((DRAFT,), None, True, False), ((), DRAFT, True, False),
    ((DRAFT, URL + '/publish/post/456'), DRAFT, True, False),
    ((URL + '/publish/post/456',), DRAFT, True, False),
])
def test_reconcile_command_preserves_uncertain_attempts(project, browser, actions, monkeypatch,
                                                       capsys, matches, known, link, linked):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    attempt = repo.begin_attempt(story, 'substack')
    failed = repo.mark_failed(attempt.id, 'Original failure evidence', draft_url=known,
                             needs_reconciliation=True)
    before = {p: p.read_bytes() for p in (project / 'In').iterdir()}
    monkeypatch.setattr(reconcile, 'inspect_drafts', lambda *_: reconcile.DraftEvidence(matches, 'Listing inspected.'))
    assert main(['substack-reconcile'] + (['--link'] if link else [])) == 0
    output = capsys.readouterr().out
    saved = record(project)
    if linked:
        assert saved.status == Status.DRAFT_CREATED and not saved.needs_reconciliation
        assert saved.error_message is None
    else:
        assert saved == failed
        assert ('Unknown' in output) if not (matches == (DRAFT,) and known == DRAFT) else ('--link' in output)
    assert before == {p: p.read_bytes() for p in (project / 'In').iterdir()}
    actions['open_new_post'].assert_not_called()
    actions['enter_title'].assert_not_called()
    browser[0].reset_mock()
    assert main(['substack']) == 1
    browser[0].assert_not_called()


def test_reconcile_rejects_stale_evidence(project):
    from publish_to_all.state import StateError
    repo = repository(project)
    attempt = repo.begin_attempt(load_story(project / 'In'), 'substack')
    failed = repo.mark_failed(attempt.id, 'Keep history', draft_url=DRAFT, needs_reconciliation=True)
    repo.reconcile_failed_draft(failed, DRAFT)
    with pytest.raises(StateError, match='stale'):
        repo.reconcile_failed_draft(failed, DRAFT)


def test_open_failure_diagnostics_output(project, browser, actions, monkeypatch, capsys):
    from publish_to_all.browser.substack import SessionDiagnostics
    monkeypatch.setattr('publish_to_all.publishers.substack.collect_diagnostics',
                        lambda *_: SessionDiagnostics(URL + '/publish/home', 'Dashboard', ('Write', 'Posts'), None))
    actions['open_new_post'].side_effect = RuntimeError('secret')
    assert main(['substack']) == 1
    output = capsys.readouterr().err
    for text in ('Final URL: ' + URL + '/publish/home', 'Page title: Dashboard', 'Visible controls: Write, Posts',
                 'Diagnostic screenshot:', 'Unavailable'):
        assert text in output
    assert 'secret' not in output
    assert 'Visible controls: Write, Posts' in record(project).error_message


def test_reconcile_missing_listing_is_unknown(project, browser, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    attempt = repo.begin_attempt(load_story(project / 'In'), 'substack')
    failed = repo.mark_failed(attempt.id, 'Original evidence', needs_reconciliation=True)
    def unavailable(*args):
        raise BrowserSessionError('Unrecognized listing')
    monkeypatch.setattr(reconcile, 'inspect_drafts', unavailable)
    assert main(['substack-reconcile', '--link']) == 0
    assert 'Unknown' in capsys.readouterr().out
    assert record(project) == failed


@pytest.mark.parametrize('value', [
    'not a URL',
    'http://example.substack.com/publish/post/123',
    'https://example.com/publish/post/123',
    'https://other.substack.com/publish/post/123',
    'https://example.substack.com/p/story-slug',
    'https://user:secret@example.substack.com/publish/post/123',
    'https://example.substack.com:8443/publish/post/123',
])
def test_supplied_draft_url_rejects_malformed_nonpublication_and_public_urls(value):
    from publish_to_all.browser.reconcile import validate_supplied_draft_url
    with pytest.raises(BrowserSessionError, match='Local state unchanged'):
        validate_supplied_draft_url(value, URL)


def test_supplied_draft_url_is_normalized_without_sensitive_components():
    from publish_to_all.browser.reconcile import validate_supplied_draft_url
    assert validate_supplied_draft_url(DRAFT + '/?token=secret#private', URL) == DRAFT


def test_invalid_supplied_url_does_not_open_browser_or_change_state(project, browser, actions, capsys):
    repo = repository(project)
    attempt = repo.begin_attempt(load_story(project / 'In'), 'substack')
    failed = repo.mark_failed(attempt.id, 'Original failure', needs_reconciliation=True)
    assert main(['substack-reconcile', '--draft-url', 'https://example.com/publish/post/123']) == 1
    assert record(project) == failed
    assert 'Local state unchanged' in capsys.readouterr().err
    browser[0].assert_not_called()
    actions['open_new_post'].assert_not_called()


@pytest.mark.parametrize('title_field,editor_surface,controls,verified', [
    (True, True, ('Saved', 'Preview', 'Continue', 'Style', 'Settings'), True),
    (False, True, ('Saved', 'Preview', 'Continue'), True),
    (True, False, ('Draft', 'Preview', 'Settings'), True),
    (True, True, ('Preview',), False),
    (False, False, ('Saved', 'Preview', 'Continue', 'Settings'), False),
])
def test_rendered_supplied_editor_accepts_corroborating_current_ui_variants(
        monkeypatch, title_field, editor_surface, controls, verified):
    from types import SimpleNamespace
    from publish_to_all.browser import reconcile
    page = MagicMock(url=DRAFT)
    field_items = [MagicMock(is_visible=MagicMock(return_value=True),
                             is_editable=MagicMock(return_value=True))] if title_field else []
    monkeypatch.setattr(editor, 'title_fields', lambda *_: MagicMock(all=lambda: field_items))
    monkeypatch.setattr(reconcile, 'authentication_evidence', lambda *_: SimpleNamespace(negative=False))
    monkeypatch.setattr(reconcile, 'rate_limit_evidence', lambda *_: False)
    monkeypatch.setattr(reconcile, '_visible_editor_surface', lambda *_: editor_surface)
    monkeypatch.setattr(reconcile, '_visible_control_labels', lambda *_: controls)
    monkeypatch.setattr(reconcile, '_visible_text', lambda *_: False)
    snapshot = reconcile._supplied_draft_snapshot(page, URL, DRAFT)
    assert snapshot.evidence.verified is verified


def test_rendered_supplied_editor_rejects_published_and_unauthenticated_pages(monkeypatch):
    from types import SimpleNamespace
    from publish_to_all.browser import reconcile
    page = MagicMock(url=DRAFT)
    field = MagicMock(is_visible=MagicMock(return_value=True), is_editable=MagicMock(return_value=True))
    monkeypatch.setattr(editor, 'title_fields', lambda *_: MagicMock(all=lambda: [field]))
    monkeypatch.setattr(reconcile, 'rate_limit_evidence', lambda *_: False)
    monkeypatch.setattr(reconcile, '_visible_editor_surface', lambda *_: True)
    monkeypatch.setattr(reconcile, '_visible_control_labels',
                        lambda *_: ('Saved', 'Preview', 'Continue'))
    monkeypatch.setattr(reconcile, '_visible_text', lambda *_: True)
    monkeypatch.setattr(reconcile, 'authentication_evidence',
                        lambda *_: SimpleNamespace(negative=False))
    assert not reconcile._supplied_draft_snapshot(page, URL, DRAFT).evidence.verified

    monkeypatch.setattr(reconcile, '_visible_text', lambda *_: False)
    monkeypatch.setattr(reconcile, 'authentication_evidence',
                        lambda *_: SimpleNamespace(negative=True))
    snapshot = reconcile._supplied_draft_snapshot(page, URL, DRAFT)
    assert not snapshot.evidence.verified and 'authentication' in snapshot.evidence.reason


def test_inaccessible_supplied_editor_response_is_unverified(monkeypatch):
    from publish_to_all.browser import reconcile
    page = MagicMock(url=DRAFT)
    page.goto.return_value.ok = False
    snapshot = MagicMock()
    monkeypatch.setattr(reconcile, '_supplied_draft_snapshot', snapshot)
    evidence = reconcile.verify_supplied_draft(page, URL, DRAFT)
    assert not evidence.verified and 'inaccessible' in evidence.reason
    snapshot.assert_not_called()


def test_http_429_supplied_editor_stops_immediately(monkeypatch):
    from publish_to_all.browser import reconcile
    page = MagicMock(url=DRAFT)
    page.goto.return_value.status = 429
    page.goto.return_value.url = DRAFT
    snapshot = MagicMock()
    monkeypatch.setattr(reconcile, '_supplied_draft_snapshot', snapshot)
    evidence = reconcile.verify_supplied_draft(page, URL, DRAFT)
    assert evidence.rate_limited and not evidence.verified
    page.goto.assert_called_once_with(DRAFT, wait_until='domcontentloaded')
    page.wait_for_timeout.assert_not_called()
    snapshot.assert_not_called()


def test_immediate_rendered_rate_limit_does_not_wait_or_inspect(monkeypatch):
    from publish_to_all.browser import reconcile
    page = MagicMock(url=DRAFT)
    page.goto.return_value.ok = True
    page.goto.return_value.status = 200
    page.goto.return_value.url = DRAFT
    snapshot = MagicMock()
    monkeypatch.setattr(reconcile, 'rate_limit_evidence', lambda *_: True)
    monkeypatch.setattr(reconcile, '_supplied_draft_snapshot', snapshot)

    evidence = reconcile.verify_supplied_draft(page, URL, DRAFT)

    assert evidence.rate_limited and not evidence.verified
    page.goto.assert_called_once_with(DRAFT, wait_until='domcontentloaded')
    page.wait_for_timeout.assert_not_called()
    snapshot.assert_not_called()


def test_rendered_rate_limit_stops_without_second_inspection(monkeypatch):
    from publish_to_all.browser import reconcile
    page = MagicMock(url=DRAFT)
    page.goto.return_value.ok = True
    page.goto.return_value.status = 200
    page.goto.return_value.url = DRAFT
    snapshot = MagicMock(return_value=reconcile._EditorSnapshot(
        reconcile.SuppliedDraftEvidence(False, 'rate limited', rate_limited=True),
    ))
    monkeypatch.setattr(reconcile, '_supplied_draft_snapshot', snapshot)
    evidence = reconcile.verify_supplied_draft(page, URL, DRAFT)
    assert evidence.rate_limited
    page.goto.assert_called_once()
    page.wait_for_timeout.assert_called_once_with(1500)
    snapshot.assert_called_once()


@pytest.mark.parametrize('kind', ['inaccessible', 'published', 'ambiguous'])
def test_supplied_draft_verification_uncertainty_leaves_state_unchanged(
        project, browser, actions, monkeypatch, capsys, kind):
    from publish_to_all.browser import reconcile
    from playwright.sync_api import Error as PlaywrightError
    repo = repository(project)
    attempt = repo.begin_attempt(load_story(project / 'In'), 'substack')
    failed = repo.mark_failed(attempt.id, 'Original failure', needs_reconciliation=True)
    if kind == 'inaccessible':
        monkeypatch.setattr(reconcile, 'verify_supplied_draft', MagicMock(side_effect=PlaywrightError('unavailable')))
    else:
        reason = 'The editor shows published-post status.' if kind == 'published' else 'Ambiguous editor evidence.'
        monkeypatch.setattr(reconcile, 'verify_supplied_draft', lambda *_: reconcile.SuppliedDraftEvidence(False, reason))
    assert main(['substack-reconcile', '--draft-url', DRAFT]) == 0
    assert record(project) == failed
    output = capsys.readouterr().out
    assert 'Unable to verify supplied draft URL safely.' in output
    assert 'Local state unchanged.' in output and 'Nothing was published.' in output
    actions['open_new_post'].assert_not_called()
    actions['enter_title'].assert_not_called()
    browser[2].click.assert_not_called()
    browser[2].fill.assert_not_called()


def test_supplied_draft_failure_reports_safe_diagnostics_and_preserves_state(
        project, browser, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    attempt = repo.begin_attempt(story, 'substack')
    failed = repo.mark_failed(attempt.id, 'Original failure', needs_reconciliation=True)
    diagnostic = reconcile.SuppliedDraftDiagnostics(
        DRAFT, 'Editing newsletter - Substack', True, True,
        ('Saved', 'Preview', 'Continue', 'Settings'), False,
        runtime_paths(project).diagnostics / 'safe.png',
    )
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', lambda *_: reconcile.SuppliedDraftEvidence(
        False, 'Insufficient corroboration.', diagnostics=diagnostic,
    ))
    assert main(['substack-reconcile', '--draft-url', DRAFT]) == 0
    assert record(project) == failed
    output = capsys.readouterr().out
    for text in (
        'Final URL: ' + DRAFT, 'Page title: Editing newsletter - Substack',
        'Editable title field: Yes', 'Editable post editor: Yes',
        'Visible editor controls: Saved, Preview, Continue, Settings',
        'Publish/Send control present: No', 'Diagnostic screenshot:',
    ):
        assert text in output
    assert 'secret' not in output


def test_supplied_draft_rate_limit_skips_auth_navigation_and_preserves_state(
        project, browser, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    attempt = repo.begin_attempt(story, 'substack')
    failed = repo.mark_failed(attempt.id, 'Original failure', needs_reconciliation=True)
    check = MagicMock(return_value=reconcile.SuppliedDraftEvidence(
        False, 'Substack returned HTTP 429.', rate_limited=True,
    ))
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', check)
    assert main(['substack-reconcile', '--draft-url', DRAFT]) == 0
    assert record(project) == failed
    check.assert_called_once()
    application.verify_page.assert_not_called()
    output = capsys.readouterr().out
    assert output.startswith('RATE_LIMITED\n')
    assert 'Local state unchanged.' in output and 'Nothing was published.' in output


def test_supplied_verified_draft_links_failed_attempt_only(
        project, browser, actions, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    unrelated = repo.begin_attempt(story, 'medium')
    unrelated = repo.mark_failed(unrelated.id, 'Unrelated failure', needs_reconciliation=True)
    attempt = repo.begin_attempt(story, 'substack')
    failed = repo.mark_failed(attempt.id, 'Original failure', needs_reconciliation=True)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft',
                        lambda *_: reconcile.SuppliedDraftEvidence(True, 'Verified draft editor.'))
    assert main(['substack-reconcile', '--draft-url', DRAFT + '?tracking=removed']) == 0
    saved = record(project)
    assert saved.id == failed.id
    assert saved.status == Status.DRAFT_CREATED
    assert saved.draft_url == DRAFT
    assert saved.created_at == saved.last_attempt_at == failed.created_at
    assert saved.updated_at >= failed.updated_at
    assert saved.error_message is None
    assert not saved.needs_reconciliation
    assert repo.find_duplicate(story.source_hash, 'substack') == saved
    with pytest.raises(DuplicatePublicationError):
        repo.begin_attempt(story, 'substack')
    assert repo.get_publication(story.source_hash, 'medium') == unrelated
    output = capsys.readouterr().out
    for text in ('Substack reconciliation complete', story.metadata.title, DRAFT,
                 'Local state:\nDraft created', 'Duplicate protection remains active.', 'Nothing was published.'):
        assert text in output
    actions['open_new_post'].assert_not_called()
    actions['enter_title'].assert_not_called()
    browser[2].click.assert_not_called()
    browser[2].fill.assert_not_called()


def test_supplied_verified_draft_replaces_only_same_unresolved_attempt_url(
        project, browser, monkeypatch):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    attempt = repo.begin_attempt(story, 'substack')
    failed = repo.mark_failed(
        attempt.id, 'Original failure', draft_url=URL + '/publish/post/999',
        needs_reconciliation=True,
    )
    monkeypatch.setattr(reconcile, 'verify_supplied_draft',
                        lambda *_: reconcile.SuppliedDraftEvidence(True, 'Verified draft editor.'))
    assert main(['substack-reconcile', '--draft-url', DRAFT]) == 0
    saved = record(project)
    assert saved.id == failed.id
    assert saved.status == Status.DRAFT_CREATED
    assert saved.draft_url == DRAFT
    assert not saved.needs_reconciliation
    with pytest.raises(DuplicatePublicationError):
        repo.begin_attempt(story, 'substack')


def test_reconcile_failed_draft_accepts_previously_missing_url_and_rejects_replacement(project):
    from publish_to_all.state import StateError
    repo = repository(project)
    story = load_story(project / 'In')
    attempt = repo.begin_attempt(story, 'substack')
    failed = repo.mark_failed(attempt.id, 'Unknown outcome', needs_reconciliation=True)
    saved = repo.reconcile_failed_draft(failed, DRAFT)
    assert saved.status == Status.DRAFT_CREATED and saved.draft_url == DRAFT
    other_attempt = repo.begin_attempt(story, 'medium')
    known = repo.mark_failed(other_attempt.id, 'Unknown', draft_url='https://example.com/original',
                             needs_reconciliation=True)
    with pytest.raises(StateError, match='insufficient'):
        repo.reconcile_failed_draft(known, 'https://example.com/different')
    replaced = repo.reconcile_failed_draft(
        known, 'https://example.com/different', verified_manual_url=True,
    )
    assert replaced.status == Status.DRAFT_CREATED
    assert replaced.draft_url == 'https://example.com/different'


@pytest.mark.parametrize('text,expected', [
    ('', 'empty'), ('A short note.', 'minimal'), ('x' * 101, 'partial'),
    ('x' * 1001, 'substantial'),
])
def test_draft_body_classification(text, expected):
    from publish_to_all.browser.reconcile import classify_body
    assert classify_body(text) == expected


@pytest.mark.parametrize('image_visible,expected', [(True, True), (False, False)])
def test_editor_content_extracts_visible_title_and_image(monkeypatch, image_visible, expected):
    from publish_to_all.browser import reconcile
    field = MagicMock()
    field.is_editable.return_value = True
    surface = MagicMock()
    surface.is_visible.return_value = True
    surface.is_editable.return_value = True
    surface.inner_text.return_value = 'Body text'
    image = MagicMock()
    image.is_visible.return_value = image_visible
    page = MagicMock()
    page.locator.side_effect = [MagicMock(all=lambda: [surface]), MagicMock(all=lambda: [image])]
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Visible title')

    title, body, present = reconcile._editor_contents(page, [field])

    assert title == 'Visible title'
    assert body == 'minimal'
    assert present is expected


def test_inspect_two_drafts_is_read_only_and_reports_details(
        project, browser, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    story = load_story(project / 'In')
    repo = repository(project)
    attempt = repo.begin_attempt(story, 'substack')
    linked = repo.mark_draft_created(attempt.id, DRAFT)
    database = runtime_paths(project).database
    before = database.read_bytes()
    second = URL + '/publish/post/456'

    def verified(_page, _publication, draft_url, _diagnostics, *, rate_limit_monitor):
        assert rate_limit_monitor is not None
        return reconcile.SuppliedDraftEvidence(
            True, 'Verified.', inspection=reconcile.DraftInspection(
                draft_url, 'Visible title', 'empty', False, ('Saved',), True,
                draft_url, 'Editing post - Substack', ('Saved', 'Preview', 'Continue'),
                'none',
            ),
        )
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', verified)

    assert main(['substack-inspect-draft', '--draft-url', DRAFT]) == 0
    first_output = capsys.readouterr().out
    assert main(['substack-inspect-draft', '--draft-url', second]) == 0
    second_output = capsys.readouterr().out

    for output, draft_url in ((first_output, DRAFT), (second_output, second)):
        assert 'Verified numeric draft URL: ' + draft_url in output
        assert 'Visible title: Visible title' in output
        assert 'Editable body: empty' in output
        assert 'Cover/image appears present: No' in output
        assert 'Cover image state: None' in output
        assert 'Definitely a draft editor: Yes' in output
    assert database.read_bytes() == before
    assert record(project) == linked
    browser[2].click.assert_not_called()
    browser[2].fill.assert_not_called()


def test_unknown_cover_inspection_reports_only_safe_cover_diagnostics(
        project, browser, monkeypatch, capsys):
    from publish_to_all.browser import image, reconcile
    diagnostics = image.CoverDiagnostics(
        image_labels=('Thumbnail', 'Upload'),
        cover_like_controls=('1 File Settings thumbnail item(s)',),
        cover_preview_present=False,
        cover_add_control_present=False,
        inline_image_controls_excluded=1,
        social_preview_controls_excluded=2,
    )
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        lambda _page, _publication, draft_url, _directory, **_kwargs: reconcile.SuppliedDraftEvidence(
            True, 'Verified.', inspection=reconcile.DraftInspection(
                draft_url, 'Visible title', 'substantial', False, ('Saved',), True,
                draft_url, 'Editing post - Substack', ('Saved', 'Preview', 'Continue'),
                'unknown', diagnostics,
            ),
        ),
    )

    assert main(['substack-inspect-draft', '--draft-url', DRAFT]) == 0
    output = capsys.readouterr().out

    for text in (
        'Cover image state: Unknown', 'Relevant image labels: Thumbnail, Upload',
        'Cover-like controls: 1 File Settings thumbnail item(s)',
        'Cover preview present: No', 'Cover add/upload control present: No',
        'Inline body-image controls excluded: 1', 'Social-preview controls excluded: 2',
    ):
        assert text in output


def test_inspection_ambiguity_preserves_sqlite(project, browser, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    linked = repo.mark_draft_created(repo.begin_attempt(story, 'substack').id, DRAFT)
    database = runtime_paths(project).database
    before = database.read_bytes()
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        lambda *_, **__: reconcile.SuppliedDraftEvidence(False, 'Ambiguous editor evidence.'),
    )

    assert main(['substack-inspect-draft', '--draft-url', URL + '/publish/post/456']) == 0

    assert 'Unable to verify supplied draft URL safely.' in capsys.readouterr().out
    assert database.read_bytes() == before
    assert record(project) == linked


def test_linked_draft_reassociation_requires_flag_and_preserves_duplicate_protection(
        project, browser, monkeypatch, capsys):
    from publish_to_all.browser import reconcile
    repo = repository(project)
    story = load_story(project / 'In')
    linked = repo.mark_draft_created(repo.begin_attempt(story, 'substack').id, DRAFT)
    second = URL + '/publish/post/456'
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        lambda *_: reconcile.SuppliedDraftEvidence(True, 'Verified draft editor.'),
    )

    assert main(['substack-reconcile', '--draft-url', second]) == 0
    assert record(project) == linked
    assert 'Local association unchanged.' in capsys.readouterr().out

    assert main([
        'substack-reconcile', '--draft-url', second, '--replace-linked-draft',
    ]) == 0
    replaced = record(project)
    assert replaced.id == linked.id
    assert replaced.draft_url == second
    assert replaced.status == Status.DRAFT_CREATED
    assert repo.find_duplicate(story.source_hash, 'substack') == replaced
    with pytest.raises(DuplicatePublicationError):
        repo.begin_attempt(story, 'substack')
    assert 'No remote draft was modified or deleted.' in capsys.readouterr().out


def test_replace_linked_draft_flag_requires_url(project, browser, capsys):
    story = load_story(project / 'In')
    linked = repository(project).mark_draft_created(
        repository(project).begin_attempt(story, 'substack').id, DRAFT,
    )
    assert main(['substack-reconcile', '--replace-linked-draft']) == 1
    assert record(project) == linked
    assert 'requires --draft-url' in capsys.readouterr().err
    browser[0].assert_not_called()
