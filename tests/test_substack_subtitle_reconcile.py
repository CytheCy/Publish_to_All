from contextlib import contextmanager
from dataclasses import asdict, replace
from hashlib import sha256
import sqlite3
from unittest.mock import MagicMock

import pytest

from publish_to_all import application
from publish_to_all.browser import body, editor, reconcile, session, subtitle
from publish_to_all.browser import subtitle_reconcile as recovery
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.state import PublicationRepository, StateError, SubtitleStatus
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/218388044'
TITLE = 'Saturation of Artificial Intelligence'
DESCRIPTION = 'The exact expected subtitle.'


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    (root / 'In').mkdir(parents=True)
    (root / 'In/story.md').write_text(
        f'---\ntitle: {TITLE}\nDescription: {DESCRIPTION}\n---\nLocal body only.'
    )
    (root / 'config.toml').write_text(f'[substack]\npublication_url = "{URL}"')
    paths = runtime_paths(root)
    paths.substack_browser_profile.mkdir(parents=True)
    story = load_story(root / 'In')
    repo = PublicationRepository(paths.database)
    old = repo.mark_draft_created(repo.begin_attempt(story, 'substack').id, URL + '/publish/post/1')
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE publications SET lifecycle_status = 'retired_test_publication' WHERE id = ?",
            (old.id,),
        )
    repo.start_new_cycle(story, 'substack')
    draft = repo.mark_draft_created(repo.begin_attempt(story, 'substack').id, DRAFT)
    failed = repo.mark_subtitle_insertion_failed(repo.mark_subtitle_inserting(draft).id, 'Uncertain subtitle')
    return root, story, repo, failed


@pytest.fixture
def remote(monkeypatch):
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*args, **kwargs):
        assert kwargs == {'headless': False, 'read_only': True}
        yield context

    launcher = MagicMock(side_effect=launch)
    monkeypatch.setattr(application, 'persistent_browser', launcher)
    inspection = reconcile.DraftInspection(
        DRAFT, TITLE, 'empty', False, ('Saved',), True, DRAFT,
        'Editing post', ('Saved', 'Continue'),
    )
    verify = MagicMock(return_value=reconcile.SuppliedDraftEvidence(True, 'Verified', inspection=inspection))
    monkeypatch.setattr(recovery, 'verify_supplied_draft', verify)
    monkeypatch.setattr(body, 'rate_limit_evidence', lambda *_: False)
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    monkeypatch.setattr(editor, 'saving_visible', lambda _: False)
    title = MagicMock()
    title.get_attribute.return_value = None
    title.input_value.return_value = TITLE
    monkeypatch.setattr(editor, 'title_fields', lambda _: MagicMock(all=lambda: [title]))
    surface = MagicMock()
    surface.inner_text.return_value = ''
    surface.locator.return_value.count.return_value = 0
    monkeypatch.setattr(body, 'locate_body_surface', lambda _: surface)
    field = MagicMock()
    field.get_attribute.return_value = None
    field.input_value.return_value = DESCRIPTION
    monkeypatch.setattr(subtitle, 'subtitle_fields', lambda _: MagicMock(all=lambda: [field]))
    page.get_by_text.return_value.all.return_value = []
    return page, title, surface, field, verify, launcher


def run(project):
    root, story, _, failed = project
    return application.reconcile_substack_subtitle(
        root, story_hash=story.source_hash, cycle_id=failed.cycle_id, draft_url=DRAFT,
    )


def all_rows(repo):
    with sqlite3.connect(repo.path) as connection:
        return connection.execute('SELECT * FROM publications ORDER BY id').fetchall()


@pytest.mark.parametrize('value,classification,status', [
    (DESCRIPTION, recovery.SubtitleClassification.EXACT, SubtitleStatus.INSERTED),
    ('', recovery.SubtitleClassification.ABSENT, SubtitleStatus.NOT_STARTED),
    (' \n', recovery.SubtitleClassification.ABSENT, SubtitleStatus.NOT_STARTED),
    ('Different nonempty subtitle', recovery.SubtitleClassification.UNKNOWN, SubtitleStatus.FAILED),
    (DESCRIPTION + ' ', recovery.SubtitleClassification.UNKNOWN, SubtitleStatus.FAILED),
])
def test_remote_value_controls_only_local_subtitle(project, remote, value, classification, status):
    _, _, repo, failed = project
    page, title, surface, field, verify, _ = remote
    field.input_value.return_value = value
    before_rows = all_rows(repo)
    result = run(project)
    assert result.evidence.classification == classification
    assert result.evidence.value == value
    assert result.substack.subtitle_status == status
    assert result.substack.body_status == 'not_started'
    assert result.substack.image_status == 'image_not_started'
    assert result.substack.draft_url == DRAFT
    assert result.substack.published_url is None
    assert result.substack.subtitle_started_at == failed.subtitle_started_at
    assert result.substack.last_attempt_at == failed.last_attempt_at
    assert all_rows(repo)[0] == before_rows[0]  # Retired cycle 1 is byte-for-byte preserved.
    changed = {key for key, value in asdict(result.substack).items() if value != asdict(failed)[key]}
    assert changed <= {'subtitle_status', 'subtitle_inserted_at', 'subtitle_error_message',
                       'needs_reconciliation', 'updated_at'}
    if classification == recovery.SubtitleClassification.UNKNOWN:
        assert result.substack == failed
        assert result.database_sha256_before == result.database_sha256_after
        assert all_rows(repo) == before_rows
    else:
        assert not result.substack.needs_reconciliation
        assert result.substack.subtitle_error_message is None
        assert result.database_sha256_before != result.database_sha256_after
    assert field.input_value.call_count == 2
    assert verify.call_count == 1 and verify.call_args.args[:3] == (page, URL, DRAFT)
    for target in (page, title, surface, field):
        for method in ('click', 'focus', 'fill', 'clear', 'type', 'press', 'blur', 'evaluate',
                       'set_input_files', 'reload'):
            getattr(target, method).assert_not_called()


@pytest.mark.parametrize('failure', ['inaccessible', 'uneditable', 'changing', 'rate_limit',
                                     'late_rate_limit', 'title', 'body', 'published'])
def test_uncertain_evidence_preserves_database_and_block(project, remote, monkeypatch, failure):
    _, _, repo, failed = project
    page, title, surface, field, verify, _ = remote
    if failure == 'inaccessible':
        field.input_value.side_effect = recovery.PlaywrightError('Cannot read field')
    elif failure == 'uneditable':
        field.is_editable.return_value = False
    elif failure == 'changing':
        field.input_value.side_effect = [DESCRIPTION, '']
    elif failure == 'rate_limit':
        verify.return_value = reconcile.SuppliedDraftEvidence(False, '429', rate_limited=True)
    elif failure == 'late_rate_limit':
        page.wait_for_timeout.side_effect = lambda _: monkeypatch.setattr(
            body, 'rate_limit_evidence', lambda *_: True,
        )
    elif failure == 'title':
        title.input_value.return_value = 'Different title'
    elif failure == 'body':
        surface.inner_text.return_value = 'Unexpected body'
    elif failure == 'published':
        verify.return_value = replace(verify.return_value, inspection=replace(
            verify.return_value.inspection, canonical_public_urls=(URL + '/p/story',),
        ))
    before = repo.path.read_bytes()
    result = run(project)
    assert result.evidence.classification == recovery.SubtitleClassification.UNKNOWN
    assert result.substack == failed
    assert result.substack.needs_reconciliation
    assert result.substack.body_status == 'not_started'
    assert repo.path.read_bytes() == before
    assert result.database_sha256_before == result.database_sha256_after == sha256(before).hexdigest()
    field.fill.assert_not_called()


def test_empty_reconciliation_never_automatically_retries_and_allows_only_one_claim(project, remote, monkeypatch):
    _, story, repo, _ = project
    remote[3].input_value.return_value = ''
    retry = MagicMock(side_effect=AssertionError('No automatic insertion'))
    monkeypatch.setattr(application.SubstackSubtitlePublisher, 'add_subtitle', retry)
    monkeypatch.setattr(application.SubstackBodyPublisher, 'add_body', retry)
    monkeypatch.setattr(subtitle, 'insert_exact_subtitle', retry)
    result = run(project)
    retry.assert_not_called()
    candidate = repo.require_subtitle_candidate(story.source_hash, 'substack')
    assert candidate == result.substack
    repo.mark_subtitle_inserting(candidate)
    with pytest.raises(StateError):
        repo.mark_subtitle_inserting(candidate)
    with pytest.raises(StateError):
        repo.require_subtitle_candidate(story.source_hash, 'substack')


@pytest.mark.parametrize('change', [
    "body_status = 'body_inserted'", "image_status = 'image_uploaded'",
    'final_click_attempt_count = 1', "publication_verification_status = 'pending'",
    "error_message = 'unrelated error'", "body_error_message = 'unrelated body error'",
    "image_error_message = 'unrelated image error'", "draft_url = 'https://example.substack.com/publish/post/2'",
    "lifecycle_status = 'retired_test_publication'",
])
def test_local_preflight_failure_never_opens_browser(project, remote, change):
    _, _, repo, failed = project
    with sqlite3.connect(repo.path) as connection:
        connection.execute(f'UPDATE publications SET {change} WHERE id = ?', (failed.id,))
    before = repo.path.read_bytes()
    with pytest.raises(StateError):
        run(project)
    remote[-1].assert_not_called()
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('failure', ['hash', 'cycle', 'duplicate_owner', 'audit'])
def test_exact_ownership_and_publication_history_are_required(project, remote, failure):
    root, story, repo, failed = project
    story_hash, cycle_id = story.source_hash, failed.cycle_id
    if failure == 'hash':
        story_hash = 'wrong'
    elif failure == 'cycle':
        cycle_id = 1
    else:
        with sqlite3.connect(repo.path) as connection:
            if failure == 'duplicate_owner':
                connection.execute('UPDATE publications SET draft_url = ? WHERE id = 1', (DRAFT,))
            else:
                connection.execute(
                    '''INSERT INTO publication_audit_events
                       (publication_id, event_type, recorded_at, source_hash, draft_url, evidence)
                       VALUES (?, 'final_click_attempt', 'earlier', ?, ?, 'attempt')''',
                    (failed.id, story_hash, DRAFT),
                )
    before = repo.path.read_bytes()
    with pytest.raises(StateError):
        application.reconcile_substack_subtitle(root, story_hash=story_hash, cycle_id=cycle_id, draft_url=DRAFT)
    remote[-1].assert_not_called()
    assert repo.path.read_bytes() == before


def test_transaction_rechecks_ownership_and_rejects_stale_evidence(project):
    _, story, repo, failed = project
    with sqlite3.connect(repo.path) as connection:
        connection.execute('UPDATE publications SET draft_url = ? WHERE id = 1', (DRAFT,))
    before = repo.path.read_bytes()
    with pytest.raises(StateError):
        repo.reconcile_subtitle(failed, story.source_hash, remote_value=DESCRIPTION, description=DESCRIPTION)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('value', [None, 'mismatch'])
def test_repository_refuses_ambiguous_value_without_writes(project, value):
    _, story, repo, failed = project
    before = repo.path.read_bytes()
    with pytest.raises(StateError):
        repo.reconcile_subtitle(failed, story.source_hash, remote_value=value, description=DESCRIPTION)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'])
def test_inspection_network_guard_blocks_remote_writes(method):
    route = MagicMock()
    route.request.method = method
    session.allow_read_only_request(route)
    if method in {'GET', 'HEAD', 'OPTIONS'}:
        route.continue_.assert_called_once()
        route.abort.assert_not_called()
    else:
        route.abort.assert_called_once()
        route.continue_.assert_not_called()


def test_readonly_browser_blocks_workers_websockets_and_write_requests(tmp_path, monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(session, 'sync_playwright', factory)
    driver = factory.return_value.__enter__.return_value
    context = driver.chromium.launch_persistent_context.return_value
    with session.persistent_browser(tmp_path / 'profile', tmp_path / 'diagnostics', read_only=True):
        assert driver.chromium.launch_persistent_context.call_args.kwargs['service_workers'] == 'block'
        context.route.assert_called_once_with('**/*', session.allow_read_only_request)
        socket = MagicMock()
        context.route_web_socket.call_args.args[1](socket)
        socket.close.assert_not_called()
        socket.connect_to_server.assert_not_called()


def test_command_reports_classification_checksums_and_stops(project, remote, monkeypatch, capsys):
    root, story, _, _ = project
    monkeypatch.chdir(root)
    assert main(['substack-subtitle-reconcile', '--story-hash', story.source_hash,
                 '--cycle-id', '2', '--draft-url', DRAFT]) == 0
    output = capsys.readouterr().out
    assert 'SUBTITLE PRESENT AND EXACT' in output
    assert 'SQLite SHA-256 before:' in output and 'SQLite SHA-256 after:' in output
    assert 'Subtitle retried: No' in output and 'Body insertion attempted: No' in output
