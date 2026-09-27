from contextlib import contextmanager
from dataclasses import replace
import hashlib
from unittest.mock import MagicMock

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import body, editor, image, reconcile
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, PublishToAllError, SubstackRateLimitError
from publish_to_all.publishers.substack import SubstackImagePublisher
from publish_to_all.state import BodyStatus, ImageStatus, PublicationRepository, StateError
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'
BODY_TEXT = 'Populated body content. ' * 20


def make_story(tmp_path, *, with_image=True, title='Exact Story'):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / 'story.md').write_text(
        f'---\ntitle: {title}\n---\n{BODY_TEXT}', encoding='utf-8',
    )
    if with_image:
        Image.new('RGB', (20, 20), 'blue').save(tmp_path / 'story.png')
    return load_story(tmp_path)


def populated_record(tmp_path, story):
    repository = PublicationRepository(tmp_path / 'publications.sqlite3')
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT)
    inserting = repository.mark_body_inserting(draft)
    return repository, repository.mark_body_inserted(inserting.id)


def publisher_setup(tmp_path, monkeypatch, *, cover_state=image.CoverState.NONE):
    story = make_story(tmp_path / 'input')
    repository, record = populated_record(tmp_path, story)
    page = MagicMock(url=DRAFT)
    title_field = MagicMock()
    title_field.is_editable.return_value = True
    title_fields = MagicMock()
    title_fields.all.return_value = [title_field]
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    control = image.CoverControl(MagicMock(), True)
    monitor = MagicMock()
    monkeypatch.setattr(editor, 'title_fields', lambda _: title_fields)
    monkeypatch.setattr(editor, 'title_value', lambda _: story.metadata.title)
    monkeypatch.setattr(editor, 'save_visible', lambda _: False)
    monkeypatch.setattr(body, 'locate_body_surface', lambda _: surface)
    monkeypatch.setattr(
        image, 'inspect_cover',
        lambda _: image.CoverInspection(
            cover_state, control if cover_state == image.CoverState.NONE else None,
        ),
    )
    monkeypatch.setattr(image, 'upload_cover', MagicMock())
    monkeypatch.setattr(image, 'confirm_cover_and_save', MagicMock())
    return (
        story, repository, record,
        SubstackImagePublisher(repository, page, URL, monitor), monitor,
    )


def test_image_candidate_requires_exact_hash_linked_draft_and_inserted_body(tmp_path):
    story = make_story(tmp_path / 'input')
    repository = PublicationRepository(tmp_path / 'state.sqlite3')
    with pytest.raises(StateError, match='exact story version'):
        repository.require_image_candidate(story.source_hash, 'substack')
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id)
    with pytest.raises(StateError, match='numeric linked draft URL'):
        repository.require_image_candidate(story.source_hash, 'substack')
    story = make_story(tmp_path / 'second-input', title='Second exact story')
    attempt = repository.begin_attempt(story, 'substack')
    repository.mark_draft_created(attempt.id, DRAFT)
    with pytest.raises(StateError, match='body must already'):
        repository.require_image_candidate(story.source_hash, 'substack')
    with pytest.raises(StateError, match='exact story version'):
        repository.require_image_candidate('wrong-hash', 'substack')


def test_image_candidate_refuses_uploaded_and_uncertain_state(tmp_path):
    story = make_story(tmp_path / 'input')
    repository, record = populated_record(tmp_path, story)
    active = repository.mark_image_uploading(record)
    with pytest.raises(StateError, match='uncertain outcome'):
        repository.require_image_candidate(story.source_hash, 'substack')
    uploaded = repository.mark_image_uploaded(active)
    assert uploaded.body_status == BodyStatus.INSERTED and uploaded.draft_url == DRAFT
    with pytest.raises(StateError, match='already recorded'):
        repository.require_image_candidate(story.source_hash, 'substack')


@pytest.mark.parametrize('cover_state', [image.CoverState.PRESENT, image.CoverState.UNKNOWN])
def test_remote_existing_or_unknown_cover_is_never_changed(
        tmp_path, monkeypatch, cover_state):
    story, repository, record, publisher, _ = publisher_setup(
        tmp_path, monkeypatch, cover_state=cover_state,
    )
    expected = 'already appears' if cover_state == image.CoverState.PRESENT else 'uncertain'
    with pytest.raises(PublishToAllError, match=expected):
        publisher.upload_image(story, record)
    image.upload_cover.assert_not_called()
    assert repository.require_image_candidate(story.source_hash, 'substack') == record


def test_cover_control_selection_prefers_one_semantic_file_input(monkeypatch):
    page = MagicMock()
    semantic = MagicMock()
    semantic.is_visible.return_value = True
    page.locator.return_value.all.return_value = []

    def locators(selector):
        locator = MagicMock()
        if selector.startswith('input[type="file"]'):
            locator.all.return_value = [semantic]
        else:
            locator.all.return_value = []
        return locator

    page.locator.side_effect = locators
    monkeypatch.setattr(image, '_semantic_input', lambda item: item is semantic)
    monkeypatch.setattr(image, '_role_controls', lambda *_: [])
    inspection = image.inspect_cover(page)
    assert inspection.state == image.CoverState.NONE
    assert inspection.control == image.CoverControl(semantic, True)


def test_ambiguous_cover_controls_are_unknown(monkeypatch):
    page = MagicMock()
    page.locator.return_value.all.return_value = []
    first, second = MagicMock(), MagicMock()

    def controls(_page, pattern):
        return [first, second] if pattern is image.ADD_COVER else []

    monkeypatch.setattr(image, '_role_controls', controls)
    assert image.inspect_cover(page).state == image.CoverState.UNKNOWN


def classifier_setup(monkeypatch, current=None):
    page = MagicMock()
    page.locator.return_value.all.return_value = []
    monkeypatch.setattr(
        image, '_current_thumbnail_evidence',
        lambda _: current or image._CurrentThumbnailEvidence(),
    )
    monkeypatch.setattr(image, '_role_controls', lambda *_: [])
    monkeypatch.setattr(image, '_excluded_control_count', lambda *_: 0)
    return page


def test_current_file_settings_thumbnail_upload_is_positive_no_cover_evidence(monkeypatch):
    control = MagicMock()
    current = image._CurrentThumbnailEvidence(
        controls=(image.CoverControl(control, True),), upload_control_count=1,
        labels=('Thumbnail', 'Upload'), recognized_items=1,
    )
    page = classifier_setup(monkeypatch, current)

    inspection = image.inspect_cover(page)

    assert inspection.state == image.CoverState.NONE
    assert inspection.control == image.CoverControl(control, True)
    assert inspection.diagnostics.cover_add_control_present
    assert inspection.diagnostics.image_labels == ('Thumbnail', 'Upload')


@pytest.mark.parametrize('preview_count,present_control_count', [(1, 0), (0, 1)])
def test_current_thumbnail_preview_or_change_remove_control_is_present(
        monkeypatch, preview_count, present_control_count):
    current = image._CurrentThumbnailEvidence(
        preview_count=preview_count, present_control_count=present_control_count,
        labels=('Thumbnail',), recognized_items=1,
    )
    inspection = image.inspect_cover(classifier_setup(monkeypatch, current))
    assert inspection.state == image.CoverState.PRESENT
    assert inspection.diagnostics.cover_preview_present is bool(preview_count)


def test_current_thumbnail_without_complete_add_or_present_evidence_stays_unknown(monkeypatch):
    current = image._CurrentThumbnailEvidence(
        upload_control_count=1, labels=('Thumbnail', 'Upload'), recognized_items=1,
    )
    assert image.inspect_cover(
        classifier_setup(monkeypatch, current),
    ).state == image.CoverState.UNKNOWN


def test_inline_image_control_is_excluded_and_does_not_establish_no_cover(monkeypatch):
    page = classifier_setup(monkeypatch)
    monkeypatch.setattr(
        image, '_excluded_control_count',
        lambda _page, selector: 1 if 'Insert image' in selector else 0,
    )
    inspection = image.inspect_cover(page)
    assert inspection.state == image.CoverState.UNKNOWN
    assert inspection.diagnostics.inline_image_controls_excluded == 1
    assert not inspection.diagnostics.cover_add_control_present


def test_social_preview_control_is_excluded_and_does_not_establish_no_cover(monkeypatch):
    page = classifier_setup(monkeypatch)
    monkeypatch.setattr(
        image, '_excluded_control_count',
        lambda _page, selector: 1 if 'social-preview' in selector else 0,
    )
    inspection = image.inspect_cover(page)
    assert inspection.state == image.CoverState.UNKNOWN
    assert inspection.diagnostics.social_preview_controls_excluded == 1
    assert not inspection.diagnostics.cover_add_control_present


def test_one_explicit_cover_add_control_is_positive_no_cover_evidence(monkeypatch):
    page = classifier_setup(monkeypatch)
    add = MagicMock()
    monkeypatch.setattr(
        image, '_role_controls',
        lambda _page, pattern: [add] if pattern is image.ADD_COVER else [],
    )
    monkeypatch.setattr(image, '_outside_non_cover_region', lambda _: True)
    inspection = image.inspect_cover(page)
    assert inspection.state == image.CoverState.NONE
    assert inspection.control == image.CoverControl(add, False)


def test_successful_upload_preserves_source_and_transitions(tmp_path, monkeypatch):
    story, repository, record, publisher, monitor = publisher_setup(tmp_path, monkeypatch)
    before = hashlib.sha256(story.image.read_bytes()).hexdigest()
    saved = publisher.upload_image(story, record)
    assert saved.image_status == ImageStatus.UPLOADED
    assert saved.body_status == BodyStatus.INSERTED
    assert saved.draft_url == DRAFT and not saved.needs_reconciliation
    assert hashlib.sha256(story.image.read_bytes()).hexdigest() == before
    image.upload_cover.assert_called_once()
    image.confirm_cover_and_save.assert_called_once()
    assert monitor.require_clear.call_count >= 6
    with pytest.raises(StateError, match='already recorded'):
        repository.require_image_candidate(story.source_hash, 'substack')


def test_title_mismatch_refuses_before_upload(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Different title')
    with pytest.raises(PublishToAllError, match='does not exactly match'):
        publisher.upload_image(story, record)
    image.upload_cover.assert_not_called()
    assert repository.require_image_candidate(story.source_hash, 'substack') == record


def test_body_not_substantial_refuses_before_upload(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(body, 'locate_body_surface', lambda _: MagicMock(
        inner_text=MagicMock(return_value='tiny'),
    ))
    with pytest.raises(PublishToAllError, match='not substantially populated'):
        publisher.upload_image(story, record)
    image.upload_cover.assert_not_called()
    assert repository.require_image_candidate(story.source_hash, 'substack') == record


def test_rate_limit_before_upload_keeps_not_started(tmp_path, monkeypatch):
    story, repository, record, publisher, monitor = publisher_setup(tmp_path, monkeypatch)
    monitor.require_clear.side_effect = SubstackRateLimitError('429')
    with pytest.raises(SubstackRateLimitError):
        publisher.upload_image(story, record)
    image.upload_cover.assert_not_called()
    assert repository.require_image_candidate(story.source_hash, 'substack') == record


@pytest.mark.parametrize('stage', ['upload', 'save'])
def test_failure_after_upload_begins_blocks_retry_and_retains_body_url(
        tmp_path, monkeypatch, stage):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    target = image.upload_cover if stage == 'upload' else image.confirm_cover_and_save
    target.side_effect = SubstackRateLimitError('429')
    with pytest.raises(PublishToAllError, match='rate limit encountered'):
        publisher.upload_image(story, record)
    saved = repository.get_publication(story.source_hash, 'substack')
    assert saved.image_status == ImageStatus.FAILED and saved.needs_reconciliation
    assert saved.body_status == BodyStatus.INSERTED and saved.draft_url == DRAFT
    assert target.call_count == 1
    with pytest.raises(StateError, match='reconciliation'):
        repository.require_image_candidate(story.source_hash, 'substack')


def test_confirmation_requires_cover_preserved_title_body_and_fresh_save(monkeypatch):
    page = MagicMock(url=DRAFT)
    title_field = MagicMock()
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    monitor = MagicMock()
    monkeypatch.setattr(editor, 'extract_draft_url', lambda *_: DRAFT)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Exact Story')
    monkeypatch.setattr(
        image, 'inspect_cover',
        lambda _: image.CoverInspection(image.CoverState.PRESENT),
    )
    monkeypatch.setattr(editor, 'save_visible', MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(image, '_saving_visible', MagicMock(side_effect=[True, False]))
    image.confirm_cover_and_save(
        page, title_field, 'Exact Story', surface, ' '.join(BODY_TEXT.split()),
        URL, DRAFT, monitor, previously_saved=True,
    )
    assert monitor.require_clear.call_count == 3
    page.goto.assert_not_called()
    page.reload.assert_not_called()


def test_confirmation_requires_image_appearance(monkeypatch):
    page = MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    monkeypatch.setattr(editor, 'extract_draft_url', lambda *_: DRAFT)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Exact Story')
    monkeypatch.setattr(
        image, 'inspect_cover',
        lambda _: image.CoverInspection(image.CoverState.NONE, MagicMock()),
    )
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    monkeypatch.setattr(image, '_saving_visible', lambda _: False)
    clock = iter([0, 1])
    monkeypatch.setattr(image, 'monotonic', lambda: next(clock))
    with pytest.raises(BrowserSessionError, match='did not appear'):
        image.confirm_cover_and_save(
            page, MagicMock(), 'Exact Story', surface, ' '.join(BODY_TEXT.split()),
            URL, DRAFT, MagicMock(), previously_saved=False, timeout=0,
        )


def test_confirmation_requires_fresh_saved_signal(monkeypatch):
    page = MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    monkeypatch.setattr(editor, 'extract_draft_url', lambda *_: DRAFT)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Exact Story')
    monkeypatch.setattr(
        image, 'inspect_cover',
        lambda _: image.CoverInspection(image.CoverState.PRESENT),
    )
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    monkeypatch.setattr(image, '_saving_visible', lambda _: False)
    clock = iter([0, 1])
    monkeypatch.setattr(image, 'monotonic', lambda: next(clock))
    with pytest.raises(BrowserSessionError, match='fresh Saved'):
        image.confirm_cover_and_save(
            page, MagicMock(), 'Exact Story', surface, ' '.join(BODY_TEXT.split()),
            URL, DRAFT, MagicMock(), previously_saved=True, timeout=0,
        )


@pytest.mark.parametrize('changed', ['title', 'body'])
def test_confirmation_rejects_changed_title_or_body(monkeypatch, changed):
    page = MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = 'changed' if changed == 'body' else BODY_TEXT
    monkeypatch.setattr(editor, 'extract_draft_url', lambda *_: DRAFT)
    monkeypatch.setattr(
        editor, 'title_value',
        lambda _: 'changed' if changed == 'title' else 'Exact Story',
    )
    with pytest.raises(BrowserSessionError, match=f'draft {changed} changed'):
        image.confirm_cover_and_save(
            page, MagicMock(), 'Exact Story', surface, ' '.join(BODY_TEXT.split()),
            URL, DRAFT, MagicMock(), previously_saved=False,
        )


def command_project(tmp_path, monkeypatch, *, with_image=True):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    story = make_story(root / 'In', with_image=with_image)
    (root / 'config.toml').write_text(f'[substack]\npublication_url = "{URL}"')
    runtime_paths(root).substack_browser_profile.mkdir(parents=True)
    repository, record = populated_record(runtime_paths(root).database.parent, story)
    return root, story, repository, record


def test_command_missing_image_refuses_before_browser(tmp_path, monkeypatch, capsys):
    command_project(tmp_path, monkeypatch, with_image=False)
    launcher = MagicMock()
    monkeypatch.setattr(application, 'persistent_browser', launcher)
    assert main(['substack-image']) == 1
    launcher.assert_not_called()
    assert 'No matching cover image' in capsys.readouterr().err


def test_command_opens_only_linked_draft_and_formats_success(tmp_path, monkeypatch, capsys):
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
            DRAFT, story.metadata.title, 'substantial', False, ('Saved',), True,
            DRAFT, 'Editing post', ('Draft', 'Saved', 'Preview', 'Settings'), 'none',
        ),
    )
    verify = MagicMock(return_value=evidence)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', verify)

    def succeed(_, exact_story, current):
        assert exact_story.source_hash == story.source_hash
        active = repository.mark_image_uploading(current)
        return repository.mark_image_uploaded(active)

    monkeypatch.setattr(application.SubstackImagePublisher, 'upload_image', succeed)
    assert main(['substack-image']) == 0
    assert verify.call_args.args[:3] == (page, URL, DRAFT)
    page.goto.assert_not_called()
    page.reload.assert_not_called()
    output = capsys.readouterr().out
    assert 'Substack draft cover image uploaded' in output
    assert 'Title:\nPreserved' in output and 'Body:\nPreserved' in output
    assert DRAFT in output and 'Nothing was published.' in output


def test_unreadable_source_refuses_before_state_change(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)

    class Unreadable:
        name = 'story.png'

        def read_bytes(self):
            raise OSError('unreadable')

    with pytest.raises(PublishToAllError, match='not readable'):
        publisher.upload_image(replace(story, image=Unreadable()), record)
    assert repository.require_image_candidate(story.source_hash, 'substack') == record
