from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from publish_to_all import application
from publish_to_all.browser import body, editor, reconcile
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, SubstackRateLimitError
from publish_to_all.publishers.substack import SubstackBodyPublisher
from publish_to_all.state import BodyStatus, PublicationRepository
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'


def formatted_story(tmp_path, body_text=None):
    text = body_text or '''# Heading

A **bold** and *italic* paragraph with a [link](https://example.com).

> Quoted text

1. First
2. Second

- One
- Two

---
'''
    (tmp_path / 'story.md').write_text(f'---\ntitle: Story\nprivate: ignored\n---\n{text}')
    return load_story(tmp_path)


@pytest.mark.parametrize('value,expected', [
    ('', body.BodyClassification.EMPTY),
    ('Start writing', body.BodyClassification.MINIMAL),
    ('x' * 101, body.BodyClassification.SUBSTANTIAL),
    (None, body.BodyClassification.UNKNOWN),
])
def test_body_classification(value, expected):
    assert body.classify_body_text(value) == expected


def test_prepared_body_excludes_front_matter_and_preserves_formatting(tmp_path):
    prepared = body.prepare_story_body(formatted_story(tmp_path))
    assert 'title: Story' not in prepared.html + prepared.text
    for fragment in ('<h1>', '<strong>', '<em>', '<a href=', '<blockquote>', '<ol>', '<ul>', '<hr'):
        assert fragment in prepared.html
    assert 'Heading' in prepared.text and 'Quoted text' in prepared.text


def test_duplicate_body_detection_uses_normalized_visible_text(tmp_path):
    prepared = body.prepare_story_body(formatted_story(tmp_path, 'A paragraph with   spacing.'))
    assert body.contains_prepared_body(' A paragraph\nwith spacing. ', prepared)
    assert not body.contains_prepared_body('Different body', prepared)


def test_large_story_uses_one_dom_insertion_without_typing(tmp_path):
    prepared = body.prepare_story_body(formatted_story(tmp_path, ('Long paragraph. ' * 2000)))
    surface = MagicMock()
    body.insert_prepared_body(surface, prepared)
    surface.evaluate.assert_called_once()
    script, payload = surface.evaluate.call_args.args
    assert 'execCommand' in script and len(payload['html']) > 10_000
    surface.press.assert_not_called()
    surface.type.assert_not_called()


def test_exactly_one_editable_body_surface_required(monkeypatch):
    monkeypatch.setattr(body, 'body_surfaces', lambda _: [])
    with pytest.raises(BrowserSessionError, match='exactly one'):
        body.locate_body_surface(MagicMock())
    monkeypatch.setattr(body, 'body_surfaces', lambda _: [MagicMock(), MagicMock()])
    with pytest.raises(BrowserSessionError, match='exactly one'):
        body.locate_body_surface(MagicMock())


def test_successful_save_confirmation_requires_fresh_signal(tmp_path, monkeypatch):
    prepared = body.prepare_story_body(formatted_story(tmp_path, 'Saved body.'))
    page = MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = 'Saved body.'
    monitor = MagicMock()
    monkeypatch.setattr(editor, 'save_visible', MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(body, '_saving_visible', MagicMock(side_effect=[True, False]))
    body.confirm_body_save(page, surface, prepared, URL, monitor, previously_saved=True)
    assert monitor.require_clear.call_count == 2
    page.goto.assert_not_called()
    page.reload.assert_not_called()


def test_stale_saved_signal_does_not_confirm(tmp_path, monkeypatch):
    prepared = body.prepare_story_body(formatted_story(tmp_path, 'Saved body.'))
    page = MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = 'Saved body.'
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    monkeypatch.setattr(body, '_saving_visible', lambda _: False)
    clock = iter([0, 31])
    monkeypatch.setattr(body, 'monotonic', lambda: next(clock))
    with pytest.raises(BrowserSessionError, match='fresh'):
        body.confirm_body_save(
            page, surface, prepared, URL, MagicMock(), previously_saved=True, timeout=30,
        )


def test_rate_limit_monitor_catches_http_and_rendered_429(monkeypatch):
    monitor = body.RateLimitMonitor(URL)
    monitor.observe(SimpleNamespace(status=429, url=DRAFT))
    with pytest.raises(SubstackRateLimitError):
        monitor.require_clear(MagicMock())
    other = body.RateLimitMonitor(URL)
    monkeypatch.setattr(body, 'rate_limit_evidence', lambda *_: True)
    with pytest.raises(SubstackRateLimitError):
        other.require_clear(MagicMock())


def test_rate_limit_monitor_ignores_untrusted_429(monkeypatch):
    monitor = body.RateLimitMonitor(URL)
    monitor.observe(SimpleNamespace(status=429, url='https://evil.example/request'))
    monkeypatch.setattr(body, 'rate_limit_evidence', lambda *_: False)
    monitor.require_clear(MagicMock())
    assert not monitor.encountered


def linked_record(tmp_path, story):
    repository = PublicationRepository(tmp_path / 'publications.sqlite3')
    attempt = repository.begin_attempt(story, 'substack')
    return repository, repository.mark_draft_created(attempt.id, DRAFT)


def publisher_setup(tmp_path, monkeypatch, *, classification=body.BodyClassification.EMPTY):
    story = formatted_story(tmp_path, 'Body to insert.')
    repository, record = linked_record(tmp_path, story)
    page = MagicMock(url=DRAFT)
    field = MagicMock()
    field.is_visible.return_value = True
    field.is_editable.return_value = True
    fields = MagicMock()
    fields.all.return_value = [field]
    surface = MagicMock()
    monitor = MagicMock()
    monkeypatch.setattr(editor, 'title_fields', lambda *_: fields)
    monkeypatch.setattr(editor, 'title_value', lambda _: story.metadata.title)
    monkeypatch.setattr(editor, 'save_visible', lambda _: False)
    monkeypatch.setattr(body, 'locate_body_surface', lambda _: surface)
    monkeypatch.setattr(
        body, 'inspect_body',
        lambda *_: body.BodyInspection(classification, '' if classification == body.BodyClassification.EMPTY else 'content'),
    )
    monkeypatch.setattr(body, 'insert_prepared_body', MagicMock())
    monkeypatch.setattr(body, 'confirm_body_save', MagicMock())
    publisher = SubstackBodyPublisher(repository, page, URL, monitor)
    return story, repository, record, publisher, monitor


def test_publisher_success_transitions_and_retains_url(tmp_path, monkeypatch):
    story, repository, record, publisher, monitor = publisher_setup(tmp_path, monkeypatch)
    saved = publisher.add_body(story, record)
    assert saved.body_status == BodyStatus.INSERTED
    assert saved.draft_url == DRAFT
    assert not saved.needs_reconciliation
    body.insert_prepared_body.assert_called_once()
    body.confirm_body_save.assert_called_once()
    assert monitor.require_clear.call_count == 8
    assert repository.find_duplicate(story.source_hash, 'substack') == saved


@pytest.mark.parametrize('classification', [
    body.BodyClassification.MINIMAL,
    body.BodyClassification.SUBSTANTIAL,
    body.BodyClassification.UNKNOWN,
])
def test_existing_or_uncertain_body_is_never_changed(tmp_path, monkeypatch, classification):
    story, repository, record, publisher, _ = publisher_setup(
        tmp_path, monkeypatch, classification=classification,
    )
    with pytest.raises(Exception, match='Manual review'):
        publisher.add_body(story, record)
    body.insert_prepared_body.assert_not_called()
    assert repository.require_body_candidate(story.source_hash, 'substack') == record


def test_visible_title_mismatch_stops_before_body_inspection(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Other title')
    inspect = MagicMock()
    monkeypatch.setattr(body, 'inspect_body', inspect)
    with pytest.raises(Exception, match='does not exactly match'):
        publisher.add_body(story, record)
    inspect.assert_not_called()
    body.insert_prepared_body.assert_not_called()
    assert repository.require_body_candidate(story.source_hash, 'substack') == record


def test_existing_exact_body_is_not_inserted_again(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        body, 'inspect_body',
        lambda *_: body.BodyInspection(body.BodyClassification.SUBSTANTIAL, 'body', True),
    )
    with pytest.raises(Exception, match='duplicate insertion'):
        publisher.add_body(story, record)
    body.insert_prepared_body.assert_not_called()
    assert repository.require_body_candidate(story.source_hash, 'substack') == record


@pytest.mark.parametrize('stage', ['insertion', 'save'])
def test_rate_limit_after_insertion_begins_blocks_retry(tmp_path, monkeypatch, stage):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    target = body.insert_prepared_body if stage == 'insertion' else body.confirm_body_save
    target.side_effect = SubstackRateLimitError('429')
    with pytest.raises(Exception, match='rate limit encountered'):
        publisher.add_body(story, record)
    saved = repository.get_publication(story.source_hash, 'substack')
    assert saved.body_status == BodyStatus.FAILED and saved.needs_reconciliation
    assert saved.draft_url == DRAFT
    assert body.insert_prepared_body.call_count == 1
    with pytest.raises(Exception, match='reconciliation'):
        repository.require_body_candidate(story.source_hash, 'substack')


def command_project(tmp_path, monkeypatch, *, draft_url=DRAFT):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'In').mkdir()
    (root / 'In/story.md').write_text('---\ntitle: Story\n---\nBody to insert.')
    (root / 'config.toml').write_text(f'[substack]\npublication_url = "{URL}"')
    runtime_paths(root).substack_browser_profile.mkdir(parents=True)
    story = load_story(root / 'In')
    repository = PublicationRepository(runtime_paths(root).database)
    attempt = repository.begin_attempt(story, 'substack')
    record = repository.mark_draft_created(attempt.id, draft_url)
    return root, story, repository, record


def fake_browser(monkeypatch):
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*_, **kwargs):
        assert kwargs == {'headless': False}
        yield context

    launcher = MagicMock(side_effect=launch)
    monkeypatch.setattr(application, 'persistent_browser', launcher)
    return launcher, page


def verified_evidence():
    return reconcile.SuppliedDraftEvidence(
        True, 'verified', inspection=reconcile.DraftInspection(
            DRAFT, 'Story', 'empty', False, ('Saved',), True,
            DRAFT, 'Editing post', ('Draft', 'Saved', 'Preview', 'Settings'),
        ),
    )


def test_command_refuses_wrong_stored_url_before_browser(tmp_path, monkeypatch, capsys):
    command_project(tmp_path, monkeypatch, draft_url='https://other.substack.com/publish/post/123')
    launcher, _ = fake_browser(monkeypatch)
    assert main(['substack-body']) == 1
    launcher.assert_not_called()
    assert 'recognized draft editor URL' in capsys.readouterr().err


@pytest.mark.parametrize('evidence', [
    reconcile.SuppliedDraftEvidence(False, 'authentication contradiction'),
    reconcile.SuppliedDraftEvidence(False, 'HTTP 429', rate_limited=True),
])
def test_command_requires_verified_authenticated_editor_before_write(
        tmp_path, monkeypatch, capsys, evidence):
    _, story, repository, record = command_project(tmp_path, monkeypatch)
    fake_browser(monkeypatch)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', lambda *_: evidence)
    add = MagicMock()
    monkeypatch.setattr(application.SubstackBodyPublisher, 'add_body', add)
    assert main(['substack-body']) == 1
    add.assert_not_called()
    assert repository.require_body_candidate(story.source_hash, 'substack') == record
    output = capsys.readouterr().err
    assert ('rate limit encountered before' in output) if evidence.rate_limited else ('safely verify' in output)


def test_command_opens_only_recorded_draft_and_formats_success(tmp_path, monkeypatch, capsys):
    _, story, repository, record = command_project(tmp_path, monkeypatch)
    _, page = fake_browser(monkeypatch)
    check = MagicMock(return_value=verified_evidence())
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', check)

    def succeed(_, _story, current):
        active = repository.mark_body_inserting(current)
        return repository.mark_body_inserted(active.id)

    monkeypatch.setattr(application.SubstackBodyPublisher, 'add_body', succeed)
    assert main(['substack-body']) == 0
    check.assert_called_once_with(page, URL, DRAFT, runtime_paths(tmp_path / 'project').diagnostics)
    page.goto.assert_not_called()
    page.reload.assert_not_called()
    output = capsys.readouterr().out
    assert 'Substack draft body added' in output
    assert 'Added:\nStory body' in output
    assert 'Not added yet:\nCover image' in output
    assert DRAFT in output and 'Nothing was published.' in output
