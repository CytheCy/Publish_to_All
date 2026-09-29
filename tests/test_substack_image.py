from contextlib import contextmanager
from dataclasses import replace
import hashlib
from unittest.mock import MagicMock

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import body, editor, image, image_observe, reconcile
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


def uncertain_image_record(repository, record, message='Earlier uncertain image failure'):
    active = repository.mark_image_uploading(record)
    return repository.mark_image_upload_failed(
        active, message, failed_step='Wait for cover preview',
    )


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
            image.CoverDiagnostics(
                image_labels=('Thumbnail', 'Upload'),
                cover_like_controls=('1 File Settings thumbnail item(s)',),
                candidate_image_inputs=2, thumbnail_image_inputs=1,
            ),
        ),
    )
    social_state = {
        image.CoverState.NONE: image_observe.SocialPreviewState.NONE,
        image.CoverState.PRESENT: image_observe.SocialPreviewState.PRESENT,
        image.CoverState.UNKNOWN: image_observe.SocialPreviewState.UNKNOWN,
    }[cover_state]
    social_target = image_observe.SocialPreviewTarget(MagicMock(), MagicMock(), MagicMock())
    monkeypatch.setattr(
        image_observe, 'inspect_social_preview_image',
        MagicMock(return_value=image_observe.SocialPreviewInspection(
            social_state, social_target if social_state == image_observe.SocialPreviewState.NONE else None,
        )),
    )
    def begin_selection(*args, **kwargs):
        kwargs['before_selection']()

    upload = MagicMock(side_effect=begin_selection)
    save = MagicMock()
    monkeypatch.setattr(image, 'open_file_settings', MagicMock())
    monkeypatch.setattr(image, 'upload_cover', upload)
    monkeypatch.setattr(image, 'confirm_cover_and_save', save)
    monkeypatch.setattr(image_observe, 'supply_social_preview_file', upload)
    monkeypatch.setattr(image_observe, 'save_social_preview', save)
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


def test_image_reconciliation_requires_failed_state_exact_hash_and_linked_draft(tmp_path):
    story = make_story(tmp_path / 'input')
    repository, record = populated_record(tmp_path, story)
    assert repository.require_image_reconciliation_candidate(
        story.source_hash, 'substack',
    ) == record
    with pytest.raises(StateError, match='exact story version'):
        repository.require_image_reconciliation_candidate('different-hash', 'substack')

    active = repository.mark_image_uploading(record)
    failed = repository.mark_image_upload_failed(active, 'uncertain')
    with repository._connection(write=True) as connection:
        connection.execute('UPDATE publications SET draft_url = NULL WHERE id = ?', (failed.id,))
    with pytest.raises(StateError, match='numeric linked draft URL'):
        repository.require_image_reconciliation_candidate(story.source_hash, 'substack')


@pytest.mark.parametrize('remote_state,expected', [
    ('none', ImageStatus.NOT_STARTED), ('present', ImageStatus.UPLOADED),
])
def test_clean_not_started_image_state_reconciles_only_positive_remote_evidence(
        tmp_path, remote_state, expected):
    story = make_story(tmp_path / 'input')
    repository, record = populated_record(tmp_path, story)

    reconciled = repository.reconcile_image_upload(record, remote_state)

    assert reconciled.image_status == expected
    assert reconciled.draft_url == record.draft_url
    assert reconciled.body_status == BodyStatus.INSERTED
    assert not reconciled.needs_reconciliation
    assert len(repository.image_attempt_history(record.id)) == (0 if remote_state == 'none' else 1)


@pytest.mark.parametrize('remote_state,expected', [
    ('none', ImageStatus.NOT_STARTED), ('present', ImageStatus.UPLOADED),
])
def test_image_reconciliation_positive_states_preserve_link_and_history(
        tmp_path, remote_state, expected):
    story = make_story(tmp_path / 'input')
    repository, record = populated_record(tmp_path, story)
    failed = uncertain_image_record(repository, record)
    original = (failed.story_id, failed.status, failed.draft_url, failed.body_status,
                failed.body_inserted_at)

    reconciled = repository.reconcile_image_upload(failed, remote_state)

    assert reconciled.image_status == expected
    assert reconciled.image_error_message is None
    assert not reconciled.needs_reconciliation
    assert (reconciled.story_id, reconciled.status, reconciled.draft_url,
            reconciled.body_status, reconciled.body_inserted_at) == original
    assert repository.find_duplicate(story.source_hash, 'substack') == reconciled
    history = repository.image_attempt_history(reconciled.id)
    assert history[0]['outcome'] == 'failed_uncertain'
    assert history[0]['error_message'] == 'Earlier uncertain image failure'
    assert history[0]['failed_step'] == 'Wait for cover preview'
    assert history[-1]['outcome'] == f'reconciled_{remote_state}'
    if remote_state == 'none':
        assert repository.require_image_candidate(story.source_hash, 'substack') == reconciled
    else:
        with pytest.raises(StateError, match='already recorded'):
            repository.require_image_candidate(story.source_hash, 'substack')


def test_unknown_image_reconciliation_evidence_does_not_change_state(tmp_path):
    story = make_story(tmp_path / 'input')
    repository, record = populated_record(tmp_path, story)
    failed = uncertain_image_record(repository, record)
    with pytest.raises(StateError, match='not positive'):
        repository.reconcile_image_upload(failed, 'unknown')
    assert repository.get_publication(story.source_hash, 'substack') == failed


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


def thumbnail_dom(monkeypatch, inputs, *, visible=True, extra_items=()):
    page, sidebar, item = MagicMock(), MagicMock(), MagicMock()
    sidebar.is_visible.return_value = True
    item.is_visible.return_value = True

    def page_locator(selector):
        result = MagicMock()
        if selector == image.CURRENT_FILE_SIDEBAR:
            result.all.return_value = [sidebar]
        elif selector == 'input[type="file"]':
            result.all.return_value = [MagicMock(), *inputs]
        else:
            result.all.return_value = []
        return result

    def sidebar_locator(selector):
        result = MagicMock()
        result.all.return_value = [item, *extra_items] if selector == '.file-sidebar-item' else []
        return result

    def item_locator(selector):
        result = MagicMock()
        result.all.return_value = inputs if selector == 'input[type="file"]' else []
        return result

    page.locator.side_effect = page_locator
    sidebar.locator.side_effect = sidebar_locator
    item.locator.side_effect = item_locator

    def visible_text(scope, pattern):
        if scope is sidebar and pattern is image.CURRENT_FILE_HEADER:
            return [MagicMock()]
        if scope is item and pattern in (image.CURRENT_THUMBNAIL, image.CURRENT_UPLOAD):
            return [MagicMock()]
        return []

    monkeypatch.setattr(image, '_visible_text', visible_text)
    monkeypatch.setattr(image, '_role_controls', lambda *_: [])
    monkeypatch.setattr(image, '_thumbnail_preview_count', lambda _: 0)
    monkeypatch.setattr(image, '_upload_progress', lambda _: False)
    for candidate in inputs:
        candidate.get_attribute.side_effect = lambda name, _visible=visible: (
            'image/png,image/jpeg' if name == 'accept' else None
        )
        candidate.evaluate.return_value = {
            'attached': True, 'enabled': True, 'visible': visible, 'files': 0,
        }
    return page, sidebar, item


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


def test_file_settings_thumbnail_input_wins_among_multiple_image_inputs(monkeypatch):
    thumbnail = MagicMock()
    page, _, _ = thumbnail_dom(monkeypatch, [thumbnail])
    unrelated = MagicMock()
    monkeypatch.setattr(image, '_semantic_input', lambda candidate: candidate is unrelated)
    monkeypatch.setattr(image, '_outside_non_cover_region', lambda _: True)
    original_locator = page.locator.side_effect

    def locators(selector):
        if selector.startswith('input[type="file"][accept'):
            result = MagicMock()
            result.all.return_value = [unrelated]
            return result
        if selector == 'input[type="file"]':
            result = MagicMock()
            result.all.return_value = [unrelated, thumbnail]
            return result
        return original_locator(selector)

    page.locator.side_effect = locators
    unrelated.evaluate.return_value = {
        'attached': True, 'enabled': True, 'visible': False, 'files': 0,
    }
    unrelated.get_attribute.side_effect = lambda name: 'image/*' if name == 'accept' else None
    inspection = image.inspect_cover(page)
    assert inspection.state == image.CoverState.NONE
    assert inspection.control.locator is thumbnail
    assert inspection.diagnostics.candidate_image_inputs == 2


def test_detached_thumbnail_input_is_rejected(monkeypatch):
    stale = MagicMock()
    page, _, _ = thumbnail_dom(monkeypatch, [stale])
    stale.evaluate.return_value = {
        'attached': False, 'enabled': True, 'visible': False, 'files': 0,
    }
    evidence = image._current_thumbnail_evidence(page)
    assert evidence.thumbnail_inputs == 1
    assert evidence.controls == ()


def test_hidden_attached_thumbnail_input_is_supported(monkeypatch):
    hidden = MagicMock()
    page, _, item = thumbnail_dom(monkeypatch, [hidden], visible=False)
    evidence = image._current_thumbnail_evidence(page)
    assert len(evidence.controls) == 1
    assert evidence.controls[0].locator is hidden
    assert evidence.controls[0].scope is item
    assert evidence.controls[0].upload_control is not None
    assert evidence.input_attached and evidence.input_enabled
    assert evidence.input_visible is False


def test_thumbnail_search_is_scoped_to_file_settings_panel(monkeypatch):
    thumbnail = MagicMock()
    outside = MagicMock()
    page, sidebar, _ = thumbnail_dom(monkeypatch, [thumbnail], extra_items=(outside,))
    outside.is_visible.return_value = True
    evidence = image._current_thumbnail_evidence(page, sidebar)
    assert evidence.controls[0].locator is thumbnail
    outside.locator.assert_not_called()


def test_open_file_settings_reuses_visible_panel_without_click(monkeypatch):
    page, sidebar, _ = thumbnail_dom(monkeypatch, [MagicMock()])
    controls = MagicMock(side_effect=AssertionError('visible panel should not be reopened'))
    monkeypatch.setattr(image, '_role_controls', controls)
    assert image.open_file_settings(page) is sidebar
    controls.assert_not_called()


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
    assert monitor.require_clear.call_count >= 4
    with pytest.raises(StateError, match='already recorded'):
        repository.require_image_candidate(story.source_hash, 'substack')


def test_upload_uses_dedicated_social_source_and_never_attachment_thumbnail(
        tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    social = story.source.parent / 'Social.png'
    Image.new('RGB', (1200, 630), 'red').save(social)
    story = load_story(story.source.parent)
    legacy_upload = MagicMock(side_effect=AssertionError('legacy thumbnail path used'))
    monkeypatch.setattr(image, 'upload_cover', legacy_upload)

    publisher.upload_image(story, record)

    image_observe.supply_social_preview_file.assert_called_once()
    assert image_observe.supply_social_preview_file.call_args.args[2] == social
    image.open_file_settings.assert_not_called()
    legacy_upload.assert_not_called()


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


@pytest.mark.parametrize('changed', ['title', 'body'])
def test_social_preview_upload_rejects_changed_title_or_body_after_save(
        tmp_path, monkeypatch, changed):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    if changed == 'title':
        titles = iter([story.metadata.title, 'Changed title'])
        monkeypatch.setattr(editor, 'title_value', lambda _: next(titles))
    else:
        initial = MagicMock(inner_text=MagicMock(return_value=BODY_TEXT))
        final = MagicMock(inner_text=MagicMock(return_value=BODY_TEXT + ' changed'))
        surfaces = iter([initial, final])
        monkeypatch.setattr(body, 'locate_body_surface', lambda _: next(surfaces))

    with pytest.raises(PublishToAllError, match='Verify title/body'):
        publisher.upload_image(story, record)

    failed = repository.get_publication(story.source_hash, 'substack')
    assert failed.image_status == ImageStatus.FAILED
    assert failed.needs_reconciliation
    assert failed.body_status == BodyStatus.INSERTED and failed.draft_url == DRAFT


def test_rate_limit_before_upload_keeps_not_started(tmp_path, monkeypatch):
    story, repository, record, publisher, monitor = publisher_setup(tmp_path, monkeypatch)
    monitor.require_clear.side_effect = SubstackRateLimitError('429')
    with pytest.raises(SubstackRateLimitError):
        publisher.upload_image(story, record)
    image.upload_cover.assert_not_called()
    assert repository.require_image_candidate(story.source_hash, 'substack') == record


def test_failure_before_file_selection_keeps_image_retryable(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    image.upload_cover.side_effect = BrowserSessionError('observer setup failed')
    with pytest.raises(PublishToAllError, match='before file selection began'):
        publisher.upload_image(story, record)
    assert repository.require_image_candidate(story.source_hash, 'substack') == record


@pytest.mark.parametrize('stage', ['upload', 'save'])
def test_failure_after_upload_begins_blocks_retry_and_retains_body_url(
        tmp_path, monkeypatch, stage):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    target = image.upload_cover if stage == 'upload' else image.confirm_cover_and_save
    def fail(*args, **kwargs):
        if stage == 'upload':
            kwargs['before_selection']()
        raise SubstackRateLimitError('429')

    target.side_effect = fail
    with pytest.raises(PublishToAllError, match='rate limit encountered'):
        publisher.upload_image(story, record)
    saved = repository.get_publication(story.source_hash, 'substack')
    assert saved.image_status == ImageStatus.FAILED and saved.needs_reconciliation
    assert saved.body_status == BodyStatus.INSERTED and saved.draft_url == DRAFT
    assert target.call_count == 1
    with pytest.raises(StateError, match='reconciliation'):
        repository.require_image_candidate(story.source_hash, 'substack')


@pytest.mark.parametrize('stage,expected_step', [
    ('upload', 'Supply file'), ('save', 'Save Social Preview'),
])
def test_upload_failure_reports_and_audits_failed_step(
        tmp_path, monkeypatch, stage, expected_step):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)
    target = image.upload_cover if stage == 'upload' else image.confirm_cover_and_save
    def fail(*args, **kwargs):
        if stage == 'upload':
            kwargs['before_selection']()
        kwargs['trace'].start(expected_step)
        raise BrowserSessionError('safe failure')

    target.side_effect = fail
    with pytest.raises(PublishToAllError, match=expected_step):
        publisher.upload_image(story, record)
    saved = repository.get_publication(story.source_hash, 'substack')
    assert f'step: {expected_step}' in saved.image_error_message
    assert repository.image_attempt_history(saved.id)[-1]['failed_step'] == expected_step


def test_upload_failure_reports_fresh_save_checkpoint(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)

    def fail_after_preview(*args, **kwargs):
        kwargs['trace'].start('Detect fresh Saved state')
        raise BrowserSessionError('save control rerendered')

    image.confirm_cover_and_save.side_effect = fail_after_preview
    with pytest.raises(PublishToAllError, match='Detect fresh Saved state'):
        publisher.upload_image(story, record)
    saved = repository.get_publication(story.source_hash, 'substack')
    assert repository.image_attempt_history(saved.id)[-1]['failed_step'] == 'Detect fresh Saved state'


def chooser_context(chooser):
    context = MagicMock()
    context.__enter__.return_value.value = chooser
    return context


def missing_chooser_context():
    context = MagicMock()
    context.__exit__.side_effect = image.PlaywrightError('chooser suppressed')
    return context


def upload_event_setup(monkeypatch, states, events):
    monkeypatch.setattr(image, '_input_state', MagicMock(side_effect=states))
    monkeypatch.setattr(image, '_mark_input_node', MagicMock())
    monkeypatch.setattr(image, '_install_upload_diagnostics', MagicMock())
    monkeypatch.setattr(image, '_activate_upload_diagnostics', MagicMock())
    monkeypatch.setattr(image, '_same_input_node', lambda _: not events.input_replaced)
    monkeypatch.setattr(image, '_upload_events', lambda _: events)
    monkeypatch.setattr(image, '_upload_progress', lambda _: events.processing_appeared)
    monkeypatch.setattr(image, '_thumbnail_preview_count', lambda _: int(events.preview_mutated))


def test_visible_upload_uses_file_chooser_and_observes_input_change(tmp_path, monkeypatch):
    selected, upload, chooser, page = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    control = image.CoverControl(selected, True, MagicMock(), upload)
    upload_event_setup(
        monkeypatch,
        [(True, True, False, 0), (True, True, False, 1)],
        image.UploadEvents(input_events=1, change_events=1),
    )
    page.expect_file_chooser.return_value = chooser_context(chooser)
    trace = image.UploadTrace()

    image.upload_cover(page, control, tmp_path / 'cover.png', trace=trace)

    upload.click.assert_called_once_with()
    chooser.set_files.assert_called_once_with(str(tmp_path / 'cover.png'))
    selected.set_input_files.assert_not_called()
    assert any('Visible Thumbnail Upload control opened' in note for note in trace.observations)
    assert any('input event: Yes; change event: Yes' in note for note in trace.observations)


def test_hidden_input_chooser_fallback_only_after_visible_control_does_not_open(
        tmp_path, monkeypatch):
    selected, upload, chooser, page = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    control = image.CoverControl(selected, True, MagicMock(), upload)
    upload_event_setup(
        monkeypatch,
        [(True, True, False, 0), (True, True, False, 1)],
        image.UploadEvents(input_events=1, change_events=1),
    )
    page.expect_file_chooser.side_effect = [
        missing_chooser_context(), chooser_context(chooser),
    ]
    trace = image.UploadTrace()

    image.upload_cover(page, control, tmp_path / 'cover.png', trace=trace)

    upload.click.assert_called_once_with()
    selected.evaluate.assert_any_call('node => node.click()')
    chooser.set_files.assert_called_once_with(str(tmp_path / 'cover.png'))
    assert chooser.set_files.call_count == 1
    assert any('did not open a chooser' in note for note in trace.observations)


def test_input_replacement_after_upload_click_is_safe_browser_evidence(
        tmp_path, monkeypatch):
    selected = MagicMock()
    scope = MagicMock()
    upload = MagicMock()
    chooser = MagicMock()
    page = MagicMock()
    control = image.CoverControl(selected, True, scope, upload)
    upload_event_setup(
        monkeypatch,
        [(True, True, False, 0), (False, False, False, 0)],
        image.UploadEvents(input_events=1, change_events=1, input_replaced=True),
    )
    monkeypatch.setattr(image, '_replacement_input_present', lambda *_: True)
    page.expect_file_chooser.return_value = chooser_context(chooser)
    trace = image.UploadTrace()

    image.upload_cover(page, control, tmp_path / 'cover.png', trace=trace)

    chooser.set_files.assert_called_once_with(str(tmp_path / 'cover.png'))
    assert trace.current == 'Confirm file input received a file'
    assert any('replaced after selection' in note for note in trace.observations)


def test_no_processing_after_selection_is_recorded_without_retry(tmp_path, monkeypatch):
    selected, upload, chooser, page = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    control = image.CoverControl(selected, True, MagicMock(), upload)
    upload_event_setup(
        monkeypatch,
        [(True, True, False, 0), (True, True, False, 1)],
        image.UploadEvents(input_events=1, change_events=1),
    )
    page.expect_file_chooser.return_value = chooser_context(chooser)
    trace = image.UploadTrace()

    image.upload_cover(page, control, tmp_path / 'cover.png', trace=trace)

    assert chooser.set_files.call_count == 1
    assert any('processing appeared: No' in note for note in trace.observations)


def test_preview_appears_after_chooser_upload(tmp_path, monkeypatch):
    selected, upload, chooser, page = MagicMock(), MagicMock(), MagicMock(), MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    control = image.CoverControl(selected, True, MagicMock(), upload)
    upload_event_setup(
        monkeypatch,
        [(True, True, False, 0), (True, True, False, 1)],
        image.UploadEvents(input_events=1, change_events=1, preview_mutated=True),
    )
    page.expect_file_chooser.return_value = chooser_context(chooser)
    monkeypatch.setattr(editor, 'extract_draft_url', lambda *_: DRAFT)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Exact Story')
    monkeypatch.setattr(
        image, 'inspect_cover',
        lambda _: image.CoverInspection(
            image.CoverState.PRESENT,
            diagnostics=image.CoverDiagnostics(cover_preview_present=True),
        ),
    )
    monkeypatch.setattr(editor, 'save_visible', lambda _: True)
    monkeypatch.setattr(image, '_saving_visible', lambda _: False)
    trace = image.UploadTrace()

    image.upload_cover(page, control, tmp_path / 'cover.png', trace=trace)
    image.confirm_cover_and_save(
        page, MagicMock(), 'Exact Story', surface, ' '.join(BODY_TEXT.split()),
        URL, DRAFT, MagicMock(), previously_saved=False, trace=trace,
    )

    assert chooser.set_files.call_count == 1
    assert 'Detect thumbnail preview' in trace.completed
    assert any('preview DOM mutated: Yes' in note for note in trace.observations)


def test_upload_image_compatibility_reports_png_without_modifying_source(tmp_path):
    source = tmp_path / 'cover.png'
    Image.new('RGB', (1536, 171), 'blue').save(source)
    before = hashlib.sha256(source.read_bytes()).digest()

    info = image.validate_upload_image(source)

    assert (info.width, info.height, info.mime_type) == (1536, 171, 'image/png')
    assert info.size_bytes == source.stat().st_size
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_upload_image_compatibility_rejects_invalid_png(tmp_path):
    source = tmp_path / 'cover.png'
    source.write_bytes(b'not a PNG')
    with pytest.raises(BrowserSessionError, match='not a valid readable image'):
        image.validate_upload_image(source)


def test_upload_progress_and_preview_are_detected_after_rerender(monkeypatch):
    page = MagicMock(url=DRAFT)
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    monitor = MagicMock()
    trace = image.UploadTrace()
    monkeypatch.setattr(editor, 'extract_draft_url', lambda *_: DRAFT)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Exact Story')
    inspections = iter([
        image.CoverInspection(
            image.CoverState.NONE,
            diagnostics=image.CoverDiagnostics(upload_progress_present=True),
        ),
        image.CoverInspection(
            image.CoverState.PRESENT,
            diagnostics=image.CoverDiagnostics(cover_preview_present=True),
        ),
    ])
    monkeypatch.setattr(image, 'inspect_cover', lambda _: next(inspections))
    monkeypatch.setattr(editor, 'save_visible', MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(image, '_saving_visible', lambda _: False)

    image.confirm_cover_and_save(
        page, MagicMock(), 'Exact Story', surface, ' '.join(BODY_TEXT.split()),
        URL, DRAFT, monitor, previously_saved=True, trace=trace,
    )

    assert 'Detect upload/progress state' in trace.completed
    assert 'Detect thumbnail preview' in trace.completed
    assert 'Detect fresh Saved state' in trace.completed
    assert trace.current == 'Verify title/body preserved'
    assert any('indicator present' in note for note in trace.observations)


def test_failed_preview_detection_never_retries_file_selection(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)

    def one_selection(*args, **kwargs):
        kwargs['before_selection']()
        kwargs['trace'].complete('Set file', 'set_input_files completed once')
        kwargs['trace'].complete(
            'Confirm file input received a file', 'Browser input reports one selected file',
        )

    image.upload_cover.side_effect = one_selection

    def no_preview(*args, **kwargs):
        kwargs['trace'].complete(
            'Detect upload/progress state',
            'No upload/progress indicator observed; preview polling continued',
        )
        kwargs['trace'].start('Detect thumbnail preview')
        raise BrowserSessionError('The uploaded cover image did not appear in the editor.')

    image.confirm_cover_and_save.side_effect = no_preview
    with pytest.raises(PublishToAllError, match='Detect thumbnail preview'):
        publisher.upload_image(story, record)
    image.upload_cover.assert_called_once()
    assert repository.get_publication(
        story.source_hash, 'substack',
    ).image_status == ImageStatus.FAILED


@pytest.mark.parametrize('checkpoint', [
    'Confirm file input attached/enabled', 'Set file',
    'Confirm file input received a file', 'Detect upload/progress state',
    'Detect thumbnail preview', 'Detect fresh Saved state',
    'Verify title/body preserved',
])
def test_exact_active_checkpoint_is_reported(tmp_path, monkeypatch, checkpoint):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        if 'before_selection' in kwargs:
            kwargs['before_selection']()
        kwargs['trace'].start(checkpoint)
        raise BrowserSessionError('safe checkpoint failure')

    if checkpoint in {
        'Confirm file input attached/enabled', 'Set file',
        'Confirm file input received a file',
    }:
        image.upload_cover.side_effect = fail
    else:
        image.confirm_cover_and_save.side_effect = fail
    with pytest.raises(PublishToAllError, match=checkpoint):
        publisher.upload_image(story, record)
    saved = repository.get_publication(story.source_hash, 'substack')
    assert repository.image_attempt_history(saved.id)[-1]['failed_step'] == checkpoint


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
    assert 'No matching story image' in capsys.readouterr().err


def test_image_command_opens_only_linked_draft_and_formats_success(tmp_path, monkeypatch, capsys):
    root, story, repository, record = command_project(tmp_path, monkeypatch)
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    launched = []

    @contextmanager
    def launch(*_, **kwargs):
        launched.append(kwargs)
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
    assert launched == [{'headless': False}]
    assert verify.call_args.args[:3] == (page, URL, DRAFT)
    page.goto.assert_not_called()
    page.reload.assert_not_called()
    output = capsys.readouterr().out
    assert 'Substack Social Preview image uploaded' in output
    assert repository.get_publication(
        story.source_hash, 'substack',
    ).image_status == ImageStatus.UPLOADED
    assert 'Nothing was published.' in output


def reconciliation_command_setup(tmp_path, monkeypatch, cover_state, *, uncertain=True):
    root, story, repository, record = command_project(tmp_path, monkeypatch)
    failed = uncertain_image_record(repository, record) if uncertain else record
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
            DRAFT, story.metadata.title, 'substantial', cover_state == 'present',
            ('Saved',), True, DRAFT, 'Editing post',
            ('Draft', 'Saved', 'Preview', 'Settings'), cover_state,
        ),
    )
    verify = MagicMock(return_value=evidence)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', verify)
    monkeypatch.setattr(
        image_observe, 'inspect_social_preview_image',
        MagicMock(return_value=image_observe.SocialPreviewInspection(
            image_observe.SocialPreviewState(cover_state),
        )),
    )
    upload = MagicMock(side_effect=AssertionError('reconciliation attempted an upload'))
    monkeypatch.setattr(image, 'upload_cover', upload)
    monkeypatch.setattr(application.SubstackImagePublisher, 'upload_image', upload)
    return story, repository, failed, page, verify, upload


def test_image_reconcile_command_accepts_clean_not_started_state(
        tmp_path, monkeypatch, capsys):
    story, repository, record, _, _, upload = reconciliation_command_setup(
        tmp_path, monkeypatch, 'present', uncertain=False,
    )

    assert main([
        'substack-image-reconcile', '--story-hash', story.source_hash,
    ]) == 0

    updated = repository.get_publication(story.source_hash, 'substack')
    assert updated.image_status == ImageStatus.UPLOADED
    assert updated.draft_url == record.draft_url
    assert updated.body_status == BodyStatus.INSERTED
    upload.assert_not_called()
    assert 'Remote draft was not modified.' in capsys.readouterr().out


@pytest.mark.parametrize('cover_state,expected_status', [
    ('none', ImageStatus.NOT_STARTED), ('present', ImageStatus.UPLOADED),
])
def test_image_reconcile_command_reads_only_linked_draft_and_updates_local_state(
        tmp_path, monkeypatch, capsys, cover_state, expected_status):
    story, repository, failed, page, verify, upload = reconciliation_command_setup(
        tmp_path, monkeypatch, cover_state,
    )

    assert main([
        'substack-image-reconcile', '--story-hash', story.source_hash,
    ]) == 0

    updated = repository.get_publication(story.source_hash, 'substack')
    assert updated.image_status == expected_status
    assert updated.image_error_message is None and not updated.needs_reconciliation
    assert updated.draft_url == failed.draft_url
    assert updated.body_status == failed.body_status
    verify.assert_called_once()
    assert verify.call_args.args[:3] == (page, URL, DRAFT)
    upload.assert_not_called()
    output = capsys.readouterr().out
    assert f'Remote Social Preview image:\n{cover_state}' in output
    assert 'Remote draft was not modified.' in output
    assert 'No image was uploaded.' in output


def test_image_reconcile_command_unknown_keeps_failure_and_blocks_retry(
        tmp_path, monkeypatch, capsys):
    story, repository, failed, _, _, upload = reconciliation_command_setup(
        tmp_path, monkeypatch, 'unknown',
    )
    assert main([
        'substack-image-reconcile', '--story-hash', story.source_hash,
    ]) == 1
    assert repository.get_publication(story.source_hash, 'substack') == failed
    upload.assert_not_called()
    assert 'Remote Social Preview image state is unknown' in capsys.readouterr().err


def test_image_reconciliation_ignores_legacy_cover_thumbnail_state(
        tmp_path, monkeypatch):
    story, repository, failed, _, verify, _ = reconciliation_command_setup(
        tmp_path, monkeypatch, 'none',
    )
    verify.return_value = reconcile.SuppliedDraftEvidence(
        True, 'verified', inspection=reconcile.DraftInspection(
            DRAFT, story.metadata.title, 'substantial', True, ('Saved',), True,
            DRAFT, 'Editing post', ('Draft', 'Saved', 'Preview', 'Settings'), 'present',
        ),
    )

    assert main([
        'substack-image-reconcile', '--story-hash', story.source_hash,
    ]) == 0
    updated = repository.get_publication(story.source_hash, 'substack')
    assert updated.image_status == ImageStatus.NOT_STARTED
    assert updated.draft_url == failed.draft_url


def test_image_reconcile_command_rate_limit_stops_without_state_change(
        tmp_path, monkeypatch, capsys):
    story, repository, failed, _, verify, upload = reconciliation_command_setup(
        tmp_path, monkeypatch, 'none',
    )
    verify.return_value = reconcile.SuppliedDraftEvidence(
        False, 'HTTP 429', rate_limited=True,
    )
    assert main([
        'substack-image-reconcile', '--story-hash', story.source_hash,
    ]) == 1
    assert repository.get_publication(story.source_hash, 'substack') == failed
    upload.assert_not_called()
    assert 'rate limit encountered' in capsys.readouterr().err


def test_image_reconcile_command_requires_exact_current_hash_before_browser(
        tmp_path, monkeypatch, capsys):
    story, repository, failed, _, verify, upload = reconciliation_command_setup(
        tmp_path, monkeypatch, 'none',
    )
    assert main([
        'substack-image-reconcile', '--story-hash', 'different-hash',
    ]) == 1
    assert repository.get_publication(story.source_hash, 'substack') == failed
    verify.assert_not_called()
    upload.assert_not_called()
    assert 'does not exactly match' in capsys.readouterr().err


def test_unreadable_source_refuses_before_state_change(tmp_path, monkeypatch):
    story, repository, record, publisher, _ = publisher_setup(tmp_path, monkeypatch)

    class Unreadable:
        name = 'story.png'

        def read_bytes(self):
            raise OSError('unreadable')

    with pytest.raises(PublishToAllError, match='not readable'):
        publisher.upload_image(replace(story, substack_image=Unreadable()), record)
    assert repository.require_image_candidate(story.source_hash, 'substack') == record
