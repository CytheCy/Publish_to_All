from contextlib import contextmanager
from dataclasses import replace
import sqlite3
from unittest.mock import MagicMock

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import deleted_draft
from publish_to_all.browser.deleted_draft import DeletedDraftEvidence, DeletedDraftState
from publish_to_all.browser.reconcile import SuppliedDraftEvidence
from publish_to_all.browser.substack import Evidence
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.state import PublicationRepository, StateError
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'In').mkdir()
    (root / 'In/story.md').write_text('---\ntitle: Reset story\n---\nBody.')
    Image.new('RGB', (2, 2)).save(root / 'In/story.png')
    (root / 'config.toml').write_text(
        f'[substack]\npublication_url = "{URL}"\n'
    )
    runtime_paths(root).substack_browser_profile.mkdir(parents=True)
    return root


def populated_record(project):
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT)
    inserting = repository.mark_body_inserting(draft)
    inserted = repository.mark_body_inserted(inserting.id)
    uploading = repository.mark_image_uploading(inserted)
    uploaded = repository.mark_image_uploaded(uploading)
    return story, repository, uploaded


def install_browser_result(monkeypatch, state, reason='test evidence'):
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*args, **kwargs):
        assert kwargs == {'headless': False}
        yield context

    launcher = MagicMock(side_effect=launch)
    checker = MagicMock(return_value=DeletedDraftEvidence(state, reason, DRAFT, 404))
    monkeypatch.setattr(application, 'persistent_browser', launcher)
    monkeypatch.setattr(
        'publish_to_all.browser.deleted_draft.inspect_deleted_draft', checker,
    )
    return launcher, checker, page


def test_verified_remote_deletion_removes_obsolete_record_and_dependent_state(
        project, monkeypatch, capsys):
    story, repository, before = populated_record(project)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            '''UPDATE publications SET error_message = ?, needs_reconciliation = 1,
               body_error_message = ?, image_error_message = ? WHERE id = ?''',
            ('old draft error', 'old body error', 'old image error', before.id),
        )
    before = repository.get_publication(story.source_hash, 'substack')
    other_story = replace(story, source_hash='unrelated-hash')
    other = repository.begin_attempt(other_story, 'substack')
    other = repository.mark_failed(other.id, 'Unrelated clean failure')
    with sqlite3.connect(repository.path) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute(
            '''INSERT INTO publication_reassociations
               (publication_id, from_story_id, to_story_id, destination,
                draft_url, reassociated_at) VALUES (?, ?, ?, ?, ?, ?)''',
            (before.id, other.story_id, before.story_id, 'substack', DRAFT,
             '2026-01-01T00:00:00+00:00'),
        )
    (project / 'In/story.md').write_text(
        '---\ntitle: Reset story\n---\nGrammatically edited body.'
    )
    current_story = load_story(project / 'In')
    assert current_story.source_hash != story.source_hash
    launcher, checker, page = install_browser_result(
        monkeypatch, DeletedDraftState.DELETED,
        'The authenticated request returned HTTP 404 for the stored draft.',
    )

    assert main([
        'substack-forget-deleted-draft', '--story-hash', story.source_hash,
    ]) == 0

    assert repository.get_publication(story.source_hash, 'substack') is None
    assert repository.get_story(story.source_hash) is None
    assert repository.find_duplicate(story.source_hash, 'substack') is None
    assert repository.get_publication(current_story.source_hash, 'substack') is None
    assert repository.get_publication('unrelated-hash', 'substack') == other
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute(
            'SELECT count(*) FROM publication_image_attempts WHERE publication_id = ?',
            (before.id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            'SELECT count(*) FROM publication_reassociations WHERE publication_id = ?',
            (before.id,),
        ).fetchone()[0] == 0
    checker.assert_called_once()
    launcher.assert_called_once()
    page.goto.assert_not_called()  # the high-level verifier owns the sole navigation
    output = capsys.readouterr().out
    for text in (
        'Obsolete deleted Substack draft removed from local state', 'Reset story', DRAFT,
        f'Publication record removed:\n{before.id}', 'Story-version row removed:\nYes',
        'Nothing was published.',
    ):
        assert text in output


@pytest.mark.parametrize('state,message', [
    (DeletedDraftState.EXISTS, 'still exists remotely'),
    (DeletedDraftState.PUBLISHED, 'published post'),
    (DeletedDraftState.UNKNOWN, 'state is unknown'),
    (DeletedDraftState.AUTHENTICATION_FAILED, 'Authentication could not be verified'),
    (DeletedDraftState.RATE_LIMITED, 'rate limiting'),
])
def test_unverified_remote_states_refuse_cleanup(
        project, monkeypatch, capsys, state, message):
    story, repository, before = populated_record(project)
    install_browser_result(monkeypatch, state)

    assert main([
        'substack-forget-deleted-draft', '--story-hash', story.source_hash,
    ]) == 1

    assert repository.get_publication(story.source_hash, 'substack') == before
    assert message.lower() in capsys.readouterr().err.lower()


def test_exact_story_hash_is_required_and_checked_before_browser(
        project, monkeypatch, capsys):
    story, repository, before = populated_record(project)
    launcher, checker, _ = install_browser_result(monkeypatch, DeletedDraftState.DELETED)
    with pytest.raises(SystemExit) as missing:
        main(['substack-forget-deleted-draft'])
    assert missing.value.code == 2
    assert main([
        'substack-forget-deleted-draft', '--story-hash', 'wrong-hash',
    ]) == 1
    assert repository.get_publication(story.source_hash, 'substack') == before
    launcher.assert_not_called()
    checker.assert_not_called()
    assert 'does not exist' in capsys.readouterr().err


def test_stored_draft_url_is_required_before_browser(project, monkeypatch, capsys):
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    attempt = repository.begin_attempt(story, 'substack')
    before = repository.mark_draft_created(attempt.id)
    launcher, checker, _ = install_browser_result(monkeypatch, DeletedDraftState.DELETED)

    assert main([
        'substack-forget-deleted-draft', '--story-hash', story.source_hash,
    ]) == 1

    assert repository.get_publication(story.source_hash, 'substack') == before
    launcher.assert_not_called()
    checker.assert_not_called()
    assert 'no valid stored numeric draft url' in capsys.readouterr().err.lower()


def test_published_record_refuses_before_browser(project, monkeypatch, capsys):
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT)
    before = repository.mark_published(draft.id, URL + '/p/public')
    launcher, checker, _ = install_browser_result(monkeypatch, DeletedDraftState.DELETED)

    assert main([
        'substack-forget-deleted-draft', '--story-hash', story.source_hash,
    ]) == 1

    assert repository.get_publication(story.source_hash, 'substack') == before
    launcher.assert_not_called()
    checker.assert_not_called()
    assert 'published substack record' in capsys.readouterr().err.lower()


def test_non_draft_workflow_refuses_before_browser(project, monkeypatch, capsys):
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT)
    before = repository.mark_publishing(draft.id)
    launcher, checker, _ = install_browser_result(monkeypatch, DeletedDraftState.DELETED)

    assert main([
        'substack-forget-deleted-draft', '--story-hash', story.source_hash,
    ]) == 1

    assert repository.get_publication(story.source_hash, 'substack') == before
    launcher.assert_not_called()
    checker.assert_not_called()
    assert 'not in the draft-created workflow' in capsys.readouterr().err


def test_remote_checker_identifies_public_post_redirect(monkeypatch):
    public_url = URL + '/p/already-published'
    page = MagicMock(url=public_url)
    page.goto.return_value = MagicMock(status=200, url=public_url)
    monitor = MagicMock(encountered=False)
    monkeypatch.setattr(deleted_draft, 'rate_limit_evidence', lambda *_: False)
    monkeypatch.setattr(
        deleted_draft, '_supplied_draft_snapshot',
        lambda *_: MagicMock(evidence=SuppliedDraftEvidence(False, 'URL changed')),
    )

    result = deleted_draft.inspect_deleted_draft(page, URL, DRAFT, monitor)

    assert result.state == DeletedDraftState.PUBLISHED


def test_remote_checker_accepts_exact_missing_draft_creator_shell(monkeypatch):
    page = MagicMock(url=DRAFT)
    page.goto.return_value = MagicMock(status=200, url=DRAFT)
    page.title.return_value = 'Editing newsletter - Substack'
    monitor = MagicMock(encountered=False)
    monkeypatch.setattr(deleted_draft, 'rate_limit_evidence', lambda *_: False)
    monkeypatch.setattr(
        deleted_draft, '_supplied_draft_snapshot',
        lambda *_: MagicMock(evidence=SuppliedDraftEvidence(False, 'No editor')),
    )
    authentication = MagicMock()
    monkeypatch.setattr(deleted_draft, 'authentication_evidence', authentication)

    def visible_item(*, enabled=True):
        return MagicMock(is_visible=MagicMock(return_value=True),
                         is_enabled=MagicMock(return_value=enabled))

    def text_locator(value, *, exact=False):
        values = {'Post not found.', 'Please try selecting another draft.'}
        return MagicMock(all=MagicMock(
            return_value=[visible_item()] if exact and value in values else []
        ))

    def role_locator(role, *, name=None, exact=False):
        enabled = {'View drafts': True, 'Preview': False, 'Continue': False}
        return MagicMock(all=MagicMock(
            return_value=[visible_item(enabled=enabled[name])]
            if role == 'button' and exact and name in enabled else []
        ))

    page.get_by_text.side_effect = text_locator
    page.get_by_role.side_effect = role_locator

    result = deleted_draft.inspect_deleted_draft(page, URL, DRAFT, monitor)

    assert result.state == DeletedDraftState.DELETED
    assert 'Post not found' in result.reason
    authentication.assert_not_called()


def test_cleanup_compare_and_set_requires_exact_record_and_url(project):
    story, repository, record = populated_record(project)
    with pytest.raises(StateError, match='stale'):
        repository.remove_verified_deleted_draft(
            'different-hash', 'substack', record, DRAFT,
        )
    with pytest.raises(StateError, match='does not match'):
        repository.remove_verified_deleted_draft(
            story.source_hash, 'substack', record, URL + '/publish/post/999',
        )
    assert repository.get_publication(story.source_hash, 'substack') == record


def test_current_edited_story_remains_not_started_after_cleanup(project, monkeypatch, capsys):
    story, repository, _ = populated_record(project)
    (project / 'In/story.md').write_text(
        '---\ntitle: Reset story\n---\nGrammatically edited body.'
    )
    current_story = load_story(project / 'In')
    install_browser_result(monkeypatch, DeletedDraftState.DELETED)
    assert main([
        'substack-forget-deleted-draft', '--story-hash', story.source_hash,
    ]) == 0
    capsys.readouterr()

    assert main(['status']) == 0
    output = capsys.readouterr().out
    assert 'Substack: Not started' in output
    assert 'Draft:' not in output and 'Body:' not in output and 'Image:' not in output
    assert repository.find_duplicate(story.source_hash, 'substack') is None
    assert repository.get_publication(current_story.source_hash, 'substack') is None


def test_remote_checker_accepts_authenticated_listing_redirect_once(monkeypatch):
    page = MagicMock(url=URL + '/publish/posts')
    response = MagicMock(status=200, url=URL + '/publish/posts')
    page.goto.return_value = response
    page.get_by_role.return_value.all.return_value = []
    monitor = MagicMock(encountered=False)
    monkeypatch.setattr(deleted_draft, 'rate_limit_evidence', lambda *_: False)
    monkeypatch.setattr(
        deleted_draft, '_supplied_draft_snapshot',
        lambda *_: MagicMock(evidence=SuppliedDraftEvidence(False, 'URL changed')),
    )
    monkeypatch.setattr(
        deleted_draft, 'authentication_evidence',
        lambda *_: Evidence(positive=True),
    )

    result = deleted_draft.inspect_deleted_draft(page, URL, DRAFT, monitor)

    assert result.state == DeletedDraftState.DELETED
    assert 'Posts listing' in result.reason
    page.goto.assert_called_once_with(DRAFT, wait_until='domcontentloaded')


def test_remote_checker_stops_immediately_on_http_429(monkeypatch):
    page = MagicMock(url=DRAFT)
    page.goto.return_value = MagicMock(status=429, url=DRAFT)
    monitor = MagicMock(encountered=True)
    snapshot = MagicMock()
    monkeypatch.setattr(deleted_draft, '_supplied_draft_snapshot', snapshot)

    result = deleted_draft.inspect_deleted_draft(page, URL, DRAFT, monitor)

    assert result.state == DeletedDraftState.RATE_LIMITED
    page.goto.assert_called_once_with(DRAFT, wait_until='domcontentloaded')
    page.wait_for_timeout.assert_not_called()
    snapshot.assert_not_called()
