from contextlib import contextmanager
from unittest.mock import ANY, MagicMock

import pytest

from publish_to_all import application
from publish_to_all.browser import body, editor, reconcile, title
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, SubstackRateLimitError
from publish_to_all.publishers.substack import SubstackTitleRepairPublisher
from publish_to_all.state import PublicationRepository, StateError
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'


def make_story(tmp_path, title_text='Exact Metadata Title'):
    (tmp_path / 'story.md').write_text(
        f'---\ntitle: {title_text}\n---\nBody must remain local.'
    )
    return load_story(tmp_path)


def linked_record(tmp_path, story, draft_url=DRAFT):
    repository = PublicationRepository(tmp_path / 'publications.sqlite3')
    attempt = repository.begin_attempt(story, 'substack')
    return repository, repository.mark_draft_created(attempt.id, draft_url)


def publisher_setup(tmp_path, monkeypatch, *, remote_title='', classification=body.BodyClassification.EMPTY):
    story = make_story(tmp_path)
    repository, record = linked_record(tmp_path, story)
    page = MagicMock(url=DRAFT)
    field = MagicMock()
    inspection = title.TitleInspection(remote_title, classification)
    monkeypatch.setattr(title, 'inspect_empty_title_and_body', lambda _: (field, inspection))
    monkeypatch.setattr(editor, 'save_visible', lambda _: False)
    monkeypatch.setattr(title, 'insert_exact_title', MagicMock())
    monkeypatch.setattr(title, 'confirm_title_save', MagicMock())
    monitor = MagicMock()
    publisher = SubstackTitleRepairPublisher(repository, page, URL, monitor)
    return story, repository, record, publisher, monitor, field


def test_title_candidate_requires_exact_hash_and_linked_draft(tmp_path):
    story = make_story(tmp_path)
    repository, _ = linked_record(tmp_path, story)
    with pytest.raises(StateError, match='exact story version'):
        repository.require_title_repair_candidate('different-hash', 'substack')

    second = make_story(tmp_path, 'Second title')
    attempt = repository.begin_attempt(second, 'substack')
    repository.mark_draft_created(attempt.id)
    with pytest.raises(StateError, match='numeric linked draft URL'):
        repository.require_title_repair_candidate(second.source_hash, 'substack')


def test_empty_title_and_body_repairs_with_exact_metadata_title(tmp_path, monkeypatch):
    story, repository, record, publisher, _, field = publisher_setup(tmp_path, monkeypatch)
    updated = publisher.repair_title(story, record)
    title.insert_exact_title.assert_called_once_with(field, 'Exact Metadata Title')
    title.confirm_title_save.assert_called_once()
    assert title.confirm_title_save.call_args.args[2] == 'Exact Metadata Title'
    assert updated.draft_url == DRAFT
    assert not updated.needs_reconciliation and updated.error_message is None
    assert repository.find_duplicate(story.source_hash, 'substack') == updated


def test_nonempty_title_is_reported_and_never_overwritten(tmp_path, monkeypatch):
    story, _, record, publisher, _, _ = publisher_setup(
        tmp_path, monkeypatch, remote_title='Existing remote title',
    )
    with pytest.raises(Exception, match='Existing remote title'):
        publisher.repair_title(story, record)
    title.insert_exact_title.assert_not_called()


@pytest.mark.parametrize('classification', [
    body.BodyClassification.SUBSTANTIAL,
    body.BodyClassification.UNKNOWN,
    body.BodyClassification.MINIMAL,
])
def test_nonempty_or_uncertain_body_refuses_repair(tmp_path, monkeypatch, classification):
    story, _, record, publisher, _, _ = publisher_setup(
        tmp_path, monkeypatch, classification=classification,
    )
    with pytest.raises(Exception, match='Manual review'):
        publisher.repair_title(story, record)
    title.insert_exact_title.assert_not_called()


def test_uncertain_title_state_refuses_repair(tmp_path, monkeypatch):
    story, _, record, publisher, _, _ = publisher_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        title, 'inspect_empty_title_and_body',
        lambda _: (None, title.TitleInspection(None, body.BodyClassification.EMPTY)),
    )
    with pytest.raises(Exception, match='could not be inspected with certainty'):
        publisher.repair_title(story, record)
    title.insert_exact_title.assert_not_called()


def test_save_confirmation_requires_fresh_saved_signal_and_exact_visible_title(monkeypatch):
    page = MagicMock(url=DRAFT)
    field = MagicMock()
    monitor = MagicMock()
    monkeypatch.setattr(editor, 'title_value', MagicMock(return_value='Exact Metadata Title'))
    monkeypatch.setattr(editor, 'save_visible', MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(title, '_saving_visible', MagicMock(side_effect=[True, False]))
    title.confirm_title_save(
        page, field, 'Exact Metadata Title', URL, monitor, previously_saved=True,
    )
    assert monitor.require_clear.call_count == 2
    page.goto.assert_not_called()
    page.reload.assert_not_called()


def test_save_confirmation_rejects_visible_title_mismatch(monkeypatch):
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Changed title')
    with pytest.raises(BrowserSessionError, match='no longer matches'):
        title.confirm_title_save(
            MagicMock(url=DRAFT), MagicMock(), 'Exact Metadata Title', URL, MagicMock(),
            previously_saved=False,
        )


def test_rate_limit_before_edit_does_not_start_or_retry(tmp_path, monkeypatch):
    story, repository, record, publisher, monitor, _ = publisher_setup(tmp_path, monkeypatch)
    monitor.require_clear.side_effect = SubstackRateLimitError('429')
    with pytest.raises(Exception, match='No retry was attempted'):
        publisher.repair_title(story, record)
    title.insert_exact_title.assert_not_called()
    assert monitor.require_clear.call_count == 1
    assert repository.require_title_repair_candidate(story.source_hash, 'substack') == record


def test_verification_stops_on_monitored_429_without_retry(monkeypatch):
    page = MagicMock(url=DRAFT)
    page.goto.return_value.ok = True
    page.goto.return_value.status = 200
    page.goto.return_value.url = DRAFT
    monitor = body.RateLimitMonitor(URL)
    page.wait_for_timeout.side_effect = lambda _: setattr(monitor, 'encountered', True)
    snapshot = MagicMock()
    monkeypatch.setattr(reconcile, '_supplied_draft_snapshot', snapshot)
    evidence = reconcile.verify_supplied_draft(
        page, URL, DRAFT, rate_limit_monitor=monitor,
    )
    assert evidence.rate_limited
    page.goto.assert_called_once_with(DRAFT, wait_until='domcontentloaded')
    page.wait_for_timeout.assert_called_once_with(100)
    snapshot.assert_not_called()


def test_rate_limit_during_save_blocks_retry_and_preserves_draft(tmp_path, monkeypatch):
    story, repository, record, publisher, _, _ = publisher_setup(tmp_path, monkeypatch)
    title.confirm_title_save.side_effect = SubstackRateLimitError('429')
    with pytest.raises(Exception, match='may or may not have been saved'):
        publisher.repair_title(story, record)
    title.insert_exact_title.assert_called_once()
    title.confirm_title_save.assert_called_once()
    saved = repository.get_publication(story.source_hash, 'substack')
    assert saved.draft_url == DRAFT and saved.needs_reconciliation
    assert repository.find_duplicate(story.source_hash, 'substack') == saved
    with pytest.raises(StateError, match='requires reconciliation'):
        repository.require_title_repair_candidate(story.source_hash, 'substack')


def command_project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'In').mkdir()
    (root / 'In/story.md').write_text('---\ntitle: Exact Metadata Title\n---\nLocal body.')
    (root / 'config.toml').write_text(f'[substack]\npublication_url = "{URL}"')
    runtime_paths(root).substack_browser_profile.mkdir(parents=True)
    story = load_story(root / 'In')
    repository, record = linked_record(runtime_paths(root).database.parent, story)
    return root, story, repository, record


def test_command_opens_only_stored_draft_and_formats_success(tmp_path, monkeypatch, capsys):
    root, story, repository, record = command_project(tmp_path, monkeypatch)
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*_, **kwargs):
        assert kwargs == {'headless': False}
        yield context

    monkeypatch.setattr(application, 'persistent_browser', launch)
    evidence = reconcile.SuppliedDraftEvidence(
        True, 'verified', inspection=reconcile.DraftInspection(
            DRAFT, '', 'empty', False, ('Saved',), True, DRAFT,
            'Editing post', ('Draft', 'Saved', 'Preview', 'Settings'),
        ),
    )
    verify = MagicMock(return_value=evidence)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', verify)

    def succeed(_, exact_story, current):
        assert exact_story.source_hash == story.source_hash
        active = repository.mark_title_repairing(current)
        return repository.mark_title_repaired(active)

    monkeypatch.setattr(application.SubstackTitleRepairPublisher, 'repair_title', succeed)
    assert main(['substack-title']) == 0
    verify.assert_called_once_with(
        page, URL, DRAFT, runtime_paths(root).diagnostics, rate_limit_monitor=ANY,
    )
    page.reload.assert_not_called()
    output = capsys.readouterr().out
    assert 'Substack draft title repaired' in output
    assert 'Title:\nExact Metadata Title' in output
    assert 'Body:\nStill empty' in output
    assert DRAFT in output and 'Nothing was published.' in output
