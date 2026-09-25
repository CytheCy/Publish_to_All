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
from publish_to_all.state import PublicationRepository, PublicationStatus as Status, MIGRATIONS
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
        assert saved.error_message == failed.error_message
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
