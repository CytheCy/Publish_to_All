from contextlib import contextmanager
from threading import Event
from unittest.mock import MagicMock

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import body, editor, image_observe, reconcile
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, SubstackRateLimitError
from publish_to_all.state import PublicationRepository
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'
BODY_TEXT = 'Populated body content. ' * 20


def observation_project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    source = root / 'In'
    source.mkdir(parents=True)
    (source / 'story.md').write_text(
        f'---\ntitle: Exact Story\n---\n{BODY_TEXT}', encoding='utf-8',
    )
    Image.new('RGB', (20, 20), 'blue').save(source / 'story.png')
    (root / 'config.toml').write_text(
        f'[substack]\npublication_url = "{URL}"', encoding='utf-8',
    )
    story = load_story(source)
    paths = runtime_paths(root)
    paths.substack_browser_profile.mkdir(parents=True)
    repository = PublicationRepository(paths.database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT)
    inserting = repository.mark_body_inserting(draft)
    record = repository.mark_body_inserted(inserting.id)
    monkeypatch.chdir(root)
    return story, repository, record


@pytest.mark.parametrize(('heading', 'label', 'kwargs', 'expected'), [
    ('File Settings', 'Thumbnail', {}, image_observe.ImageFeature.ATTACHMENT_THUMBNAIL),
    ('Edit social preview', 'Image', {}, image_observe.ImageFeature.SOCIAL_POST_PREVIEW),
    ('Editor', 'Image', {'in_body': True}, image_observe.ImageFeature.INLINE_BODY),
    ('Website editor', 'Cover image', {'publication_editor': True},
     image_observe.ImageFeature.PUBLICATION_COVER),
    ('Post', 'Hero image', {}, image_observe.ImageFeature.POST_HERO),
])
def test_image_features_are_distinct(heading, label, kwargs, expected):
    assert image_observe.classify_image_feature(
        panel_heading=heading, label=label, **kwargs,
    ) == expected


def test_open_social_preview_stops_before_file_selection_and_publication_controls():
    page = MagicMock()
    monitor = MagicMock()
    settings = MagicMock()
    settings_dialog = MagicMock()
    social_label = MagicMock()
    edit = MagicMock()
    preview_dialog = MagicMock()
    heading = MagicMock()
    image_label = MagicMock()
    file_input = MagicMock()
    select = MagicMock()
    save = MagicMock()
    post_settings_heading = MagicMock()
    for item in (settings, settings_dialog, social_label, edit, preview_dialog,
                 heading, image_label, file_input, select, save, post_settings_heading):
        item.is_visible.return_value = True
        item.is_enabled.return_value = True
    settings.get_attribute.return_value = 'settings-button'
    file_input.get_attribute.side_effect = lambda name: {
        'accept': 'image/*', 'aria-label': 'Upload file',
    }.get(name)

    def page_role(role, name):
        result = MagicMock()
        result.all.return_value = [settings] if role == 'button' and name is image_observe.SETTINGS else []
        return result

    def page_locator(selector):
        result = MagicMock()
        if selector == '[role="dialog"][data-testid="settings-modal"]':
            result.all.return_value = [settings_dialog]
        elif selector == '[role="dialog"][data-testid="modal"]':
            result.all.return_value = [preview_dialog]
        else:
            result.all.return_value = []
        return result

    page.get_by_role.side_effect = page_role
    page.locator.side_effect = page_locator
    settings_dialog.get_by_text.return_value.all.return_value = [social_label]
    social_section = MagicMock()
    social_section.count.return_value = 1
    social_section.is_visible.return_value = True
    social_section.inner_text.return_value = 'Social preview Edit'
    social_label.locator.return_value = social_section

    def settings_role(role, name):
        result = MagicMock()
        if role == 'button' and name is image_observe.EDIT:
            result.all.return_value = [edit]
        elif role == 'heading' and name is image_observe.POST_SETTINGS:
            result.all.return_value = [post_settings_heading]
        else:
            result.all.return_value = []
        return result

    settings_dialog.get_by_role.side_effect = settings_role
    social_section.get_by_role.side_effect = settings_role

    def preview_role(role, name):
        result = MagicMock()
        if role == 'heading' and name is image_observe.EDIT_SOCIAL_PREVIEW:
            result.all.return_value = [heading]
        elif role == 'button' and name is image_observe.LOCAL_SAVE:
            result.all.return_value = [save]
        else:
            result.all.return_value = []
        return result

    preview_dialog.get_by_role.side_effect = preview_role
    preview_dialog.get_by_text.return_value.all.return_value = [image_label]
    def preview_locator(selector):
        result = MagicMock()
        result.all.return_value = [file_input] if selector == 'input[type="file"]' else []
        return result

    preview_dialog.locator.side_effect = preview_locator

    target = image_observe.open_social_preview_image(page, monitor)

    assert target == image_observe.SocialPreviewTarget(preview_dialog, file_input, file_input)
    settings.click.assert_called_once_with()
    edit.click.assert_called_once_with()
    select.click.assert_not_called()
    save.click.assert_not_called()
    file_input.set_input_files.assert_not_called()
    assert monitor.require_clear.call_count >= 7


def test_post_settings_uses_editor_test_id_among_generic_settings_controls():
    page = MagicMock()
    account_settings, editor_settings = MagicMock(), MagicMock()
    for control in (account_settings, editor_settings):
        control.is_visible.return_value = True
        control.is_enabled.return_value = True
    account_settings.get_attribute.return_value = None
    editor_settings.get_attribute.return_value = 'settings-button'
    page.get_by_role.return_value.all.return_value = [account_settings, editor_settings]

    assert image_observe._post_settings_controls(page) == [editor_settings]


def test_multiple_editor_settings_controls_stop_before_any_click():
    page, monitor = MagicMock(), MagicMock()
    controls = [MagicMock(), MagicMock()]
    for control in controls:
        control.is_visible.return_value = True
        control.is_enabled.return_value = True
        control.get_attribute.return_value = 'settings-button'
    page.get_by_role.return_value.all.return_value = controls

    with pytest.raises(BrowserSessionError, match='ambiguous'):
        image_observe.open_social_preview_editor(page, monitor)

    for control in controls:
        control.click.assert_not_called()


def test_navigation_waits_through_loading_and_rerender(monkeypatch):
    page, monitor = MagicMock(), MagicMock()
    settings, settings_dialog, edit, social_dialog = (
        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
    )
    settings_steps = iter([[], [settings]])
    panel_steps = iter([[], [settings_dialog]])
    edit_steps = iter([[], [edit]])
    dialog_steps = iter([[], [social_dialog]])
    monkeypatch.setattr(image_observe, '_post_settings_controls', lambda _: next(settings_steps))
    monkeypatch.setattr(image_observe, '_post_settings_dialogs', lambda _: next(panel_steps))
    monkeypatch.setattr(image_observe, '_social_preview_edit_controls', lambda _: next(edit_steps))
    monkeypatch.setattr(image_observe, '_social_preview_dialogs', lambda _: next(dialog_steps))

    assert image_observe.open_social_preview_editor(page, monitor) is social_dialog

    settings.click.assert_called_once_with()
    edit.click.assert_called_once_with()
    assert page.wait_for_timeout.call_count == 4


def test_rate_limit_stops_navigation_before_selector_lookup():
    page, monitor = MagicMock(), MagicMock()
    monitor.require_clear.side_effect = SubstackRateLimitError('429')

    with pytest.raises(SubstackRateLimitError):
        image_observe.open_social_preview_editor(page, monitor)

    page.get_by_role.assert_not_called()
    page.wait_for_timeout.assert_not_called()


def test_settings_panel_is_discovered_from_page_portal():
    page, editor_subtree = MagicMock(), MagicMock()
    dialog, heading = MagicMock(), MagicMock()
    dialog.is_visible.return_value = True
    heading.is_visible.return_value = True
    page.locator.return_value.all.return_value = [dialog]
    dialog.get_by_role.return_value.all.return_value = [heading]
    editor_subtree.locator.return_value.all.return_value = []

    assert image_observe._post_settings_dialogs(page) == [dialog]
    editor_subtree.locator.assert_not_called()


def test_duplicate_social_preview_labels_stop_without_edit_click():
    panel = MagicMock()
    labels = [MagicMock(), MagicMock()]
    for label in labels:
        label.is_visible.return_value = True
    panel.get_by_text.return_value.all.return_value = labels

    with pytest.raises(BrowserSessionError, match='ambiguous'):
        image_observe._social_preview_edit_controls(panel)

    panel.get_by_role.assert_not_called()


def test_manual_dom_mutation_capture_uses_only_safe_summary():
    page = MagicMock()
    page.evaluate.return_value = {
        'inputEvents': 1, 'changeEvents': 1, 'originalReceived': False,
        'differentReceived': True, 'differentAppeared': True,
        'originalReplaced': True, 'addedNodes': 7, 'removedNodes': 3,
        'processingAppeared': True, 'previewAppeared': True,
        'rerendered': True, 'activeChanged': True,
        'dialogReplaced': True, 'dialogTemporarilyClosed': True,
        'finalPreview': True,
        'savingAppeared': True, 'savedAppeared': True, 'inputCount': 2,
    }

    result = image_observe.read_dom_observation(page)

    assert result.different_input_received_file
    assert result.different_input_appeared and result.original_input_replaced
    assert (result.added_nodes, result.removed_nodes) == (7, 3)
    assert result.processing_appeared and result.preview_appeared
    assert result.final_preview_detected
    assert result.dialog_replaced and result.dialog_temporarily_closed
    assert result.saving_appeared and result.saved_appeared


def test_rate_limit_stops_manual_wait_immediately():
    page = MagicMock()
    monitor = MagicMock()
    monitor.require_clear.side_effect = SubstackRateLimitError('429')
    release = Event()

    with pytest.raises(SubstackRateLimitError):
        image_observe.wait_for_manual_upload(
            page, monitor, reader=lambda _: release.wait(5),
        )

    page.wait_for_timeout.assert_not_called()
    release.set()


def _ordered_wait(observations):
    remaining = iter(observations)

    def wait(_page, _monitor, predicate, **_kwargs):
        latest = None
        for latest in remaining:
            if predicate(latest):
                return latest
        return latest or image_observe.DomObservation(observed_input_count=0)

    return wait


def test_supply_file_requires_observed_transition_and_never_retries(tmp_path, monkeypatch):
    page = MagicMock()
    monitor = MagicMock()
    file_input = MagicMock()
    target = image_observe.SocialPreviewTarget(MagicMock(), file_input, MagicMock())
    observations = iter([
        image_observe.DomObservation(input_events=1, change_events=1),
        image_observe.DomObservation(
            input_events=1, change_events=1, original_input_replaced=True,
            different_input_appeared=True, processing_appeared=True,
            preview_appeared=True, final_preview_detected=True,
        ),
    ])
    monkeypatch.setattr(image_observe, '_wait_for', lambda *_, **__: next(observations))
    monkeypatch.setattr(image_observe, 'install_dom_observer', MagicMock())
    trace = image_observe.UploadTrace()
    source = tmp_path / 'story.png'

    result = image_observe.supply_social_preview_file(
        page, target, source, monitor, trace=trace,
    )

    file_input.set_input_files.assert_called_once_with(str(source))
    assert result.preview_appeared
    assert trace.completed[-3:] == [
        'Observe input/change', 'Detect upload transition', 'Detect preview',
    ]
    assert 'Original input detached: Yes' in trace.text()
    assert 'Replacement input found: Yes' in trace.text()


@pytest.mark.parametrize('transition', [
    image_observe.DomObservation(
        input_events=1, change_events=1, original_input_replaced=True,
        different_input_appeared=True, preview_appeared=True, final_preview_detected=True,
    ),
    image_observe.DomObservation(
        input_events=1, change_events=1, social_preview_rerendered=True,
        dialog_replaced=True, preview_appeared=True, final_preview_detected=True,
    ),
    image_observe.DomObservation(
        input_events=1, change_events=1, processing_appeared=True,
        preview_appeared=True, final_preview_detected=True,
    ),
    image_observe.DomObservation(
        input_events=1, change_events=1, preview_appeared=True,
        final_preview_detected=True,
    ),
])
def test_supply_file_accepts_flexible_transition_evidence(
        tmp_path, monkeypatch, transition):
    target = image_observe.SocialPreviewTarget(MagicMock(), MagicMock(), MagicMock())
    monkeypatch.setattr(image_observe, '_wait_for', _ordered_wait([
        image_observe.DomObservation(input_events=1, change_events=1), transition,
    ]))
    monkeypatch.setattr(image_observe, 'install_dom_observer', MagicMock())

    result = image_observe.supply_social_preview_file(
        MagicMock(), target, tmp_path / 'story.png', MagicMock(),
        trace=image_observe.UploadTrace(),
    )

    assert result.final_preview_detected
    target.file_input.set_input_files.assert_called_once()


def test_supply_file_allows_input_replacement_after_processing_starts(tmp_path, monkeypatch):
    target = image_observe.SocialPreviewTarget(MagicMock(), MagicMock(), MagicMock())
    monkeypatch.setattr(image_observe, '_wait_for', _ordered_wait([
        image_observe.DomObservation(input_events=1, change_events=1),
        image_observe.DomObservation(
            input_events=1, change_events=1, processing_appeared=True,
        ),
        image_observe.DomObservation(
            input_events=1, change_events=1, processing_appeared=True,
            original_input_replaced=True, different_input_appeared=True,
            preview_appeared=True, final_preview_detected=True,
        ),
    ]))
    monkeypatch.setattr(image_observe, 'install_dom_observer', MagicMock())

    result = image_observe.supply_social_preview_file(
        MagicMock(), target, tmp_path / 'story.png', MagicMock(),
        trace=image_observe.UploadTrace(),
    )

    assert result.processing_appeared and result.original_input_replaced
    target.file_input.set_input_files.assert_called_once()


def test_supply_file_requires_final_preview_and_reports_separate_diagnostics(
        tmp_path, monkeypatch):
    target = image_observe.SocialPreviewTarget(MagicMock(), MagicMock(), MagicMock())
    observation = image_observe.DomObservation(
        input_events=1, change_events=1, original_input_replaced=True,
        social_preview_rerendered=True, processing_appeared=True,
    )
    monkeypatch.setattr(image_observe, '_wait_for', lambda *_, **__: observation)
    monkeypatch.setattr(image_observe, 'install_dom_observer', MagicMock())
    trace = image_observe.UploadTrace()

    with pytest.raises(BrowserSessionError, match='preview did not appear'):
        image_observe.supply_social_preview_file(
            MagicMock(), target, tmp_path / 'story.png', MagicMock(), trace=trace,
        )

    target.file_input.set_input_files.assert_called_once()
    assert 'Original input detached: Yes' in trace.text()
    assert 'Replacement input found: No' in trace.text()
    assert 'Social Preview panel rerendered: Yes' in trace.text()
    assert 'Processing detected: Yes' in trace.text()
    assert 'Preview mutation detected: No' in trace.text()
    assert 'Final preview detected: No' in trace.text()


def test_dom_observer_re_resolves_dialog_panel_inputs_and_preview():
    file_input = MagicMock()
    target = image_observe.SocialPreviewTarget(MagicMock(), file_input, MagicMock())

    image_observe.install_dom_observer(MagicMock(), target)

    script = file_input.evaluate.call_args.args[0]
    assert 'resolveDialog' in script
    assert '[role="dialog"][data-testid="modal"]' in script
    assert 'resolveImageScope' in script
    assert 'const inputs = fileInputs(scope)' in script
    assert 'currentPreviews' in script

    page = MagicMock()
    page.evaluate.return_value = {'finalPreview': True}
    assert image_observe.read_dom_observation(page).final_preview_detected
    assert 's.refresh([])' in page.evaluate.call_args.args[0]


def test_social_preview_save_is_dialog_scoped_and_requires_fresh_save(monkeypatch):
    page = MagicMock()
    monitor = MagicMock()
    dialog = MagicMock()
    save = MagicMock()
    monkeypatch.setattr(image_observe, '_current_social_preview_dialog', lambda _: dialog)

    def controls(scope, role, name):
        assert scope is dialog and role == 'button'
        return [save] if name is image_observe.LOCAL_SAVE else []

    monkeypatch.setattr(image_observe, '_controls', controls)
    monkeypatch.setattr(image_observe, 'reset_save_observation', MagicMock())
    monkeypatch.setattr(
        image_observe, '_wait_for',
        lambda *_, **__: image_observe.DomObservation(saving_then_saved=True),
    )
    trace = image_observe.UploadTrace()

    image_observe.save_social_preview(page, monitor, trace=trace)

    save.click.assert_called_once_with()
    page.get_by_role('button', name='Continue').click.assert_not_called()
    page.get_by_role('button', name='Publish').click.assert_not_called()
    assert 'Confirm Saving → Saved' in trace.completed


def test_social_preview_save_rejects_publication_action_in_dialog(monkeypatch):
    dialog = MagicMock()
    monkeypatch.setattr(image_observe, '_current_social_preview_dialog', lambda _: dialog)
    save, publish = MagicMock(), MagicMock()
    monkeypatch.setattr(
        image_observe, '_controls',
        lambda _scope, _role, name: [save] if name is image_observe.LOCAL_SAVE else [publish],
    )
    with pytest.raises(BrowserSessionError, match='ambiguous'):
        image_observe.save_social_preview(
            MagicMock(), MagicMock(), trace=image_observe.UploadTrace(),
        )
    save.click.assert_not_called()
    publish.click.assert_not_called()


def test_social_preview_save_requires_fresh_ordered_saving_then_saved(monkeypatch):
    dialog, save = MagicMock(), MagicMock()
    monkeypatch.setattr(image_observe, '_current_social_preview_dialog', lambda _: dialog)
    monkeypatch.setattr(
        image_observe, '_controls',
        lambda _scope, _role, name: [save] if name is image_observe.LOCAL_SAVE else [],
    )
    monkeypatch.setattr(image_observe, 'reset_save_observation', MagicMock())
    monkeypatch.setattr(
        image_observe, '_wait_for',
        lambda *_, **__: image_observe.DomObservation(
            saving_appeared=True, saved_appeared=True, saving_then_saved=False,
        ),
    )
    with pytest.raises(BrowserSessionError, match='fresh Saving → Saved'):
        image_observe.save_social_preview(
            MagicMock(), MagicMock(), trace=image_observe.UploadTrace(),
        )
    save.click.assert_called_once_with()


@pytest.mark.parametrize(('inputs', 'removals', 'previews', 'expected'), [
    (1, 0, 0, image_observe.SocialPreviewState.NONE),
    (1, 1, 3, image_observe.SocialPreviewState.PRESENT),
    (2, 0, 0, image_observe.SocialPreviewState.UNKNOWN),
])
def test_social_preview_inspection_reports_present_none_unknown(
        monkeypatch, inputs, removals, previews, expected):
    dialog = MagicMock()
    dialog.inner_text.return_value = 'Edit social preview Image'
    image_label = MagicMock()
    image_label.is_visible.return_value = True
    dialog.get_by_text.return_value.all.return_value = [image_label]
    file_inputs = [MagicMock() for _ in range(inputs)]
    for candidate in file_inputs:
        candidate.get_attribute.side_effect = lambda name: {
            'accept': 'image/*', 'aria-label': 'Upload file',
        }.get(name)

    def locator(selector):
        result = MagicMock()
        items = file_inputs if selector == 'input[type="file"]' else [MagicMock() for _ in range(previews)]
        for item in items:
            item.is_visible.return_value = True
        result.all.return_value = items
        return result

    dialog.locator.side_effect = locator
    monkeypatch.setattr(image_observe, 'open_social_preview_editor', lambda *_args, **_kwargs: dialog)

    def controls(_scope, _role, name):
        if name is image_observe.REMOVE_IMAGE:
            return [MagicMock() for _ in range(removals)]
        return []

    monkeypatch.setattr(image_observe, '_controls', controls)
    result = image_observe.inspect_social_preview_image(MagicMock(), MagicMock())
    assert result.state == expected
    assert (result.target is not None) == (expected == image_observe.SocialPreviewState.NONE)


@pytest.mark.parametrize('context', [
    'File Settings Thumbnail', 'Insert image', 'Publication cover', 'attachment upload',
])
def test_social_preview_inspection_rejects_unrelated_image_context(monkeypatch, context):
    dialog = MagicMock()
    dialog.inner_text.return_value = context
    monkeypatch.setattr(image_observe, 'open_social_preview_editor', lambda *_args, **_kwargs: dialog)
    result = image_observe.inspect_social_preview_image(MagicMock(), MagicMock())
    assert result.state == image_observe.SocialPreviewState.UNKNOWN
    assert result.diagnostics.rejected_control_count == 1


def test_observation_command_opens_linked_draft_without_injection_or_state_change(
        tmp_path, monkeypatch, capsys):
    story, repository, before = observation_project(tmp_path, monkeypatch)
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*_, **kwargs):
        assert kwargs == {'headless': False}
        yield context

    monkeypatch.setattr(application, 'persistent_browser', launch)
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        MagicMock(return_value=reconcile.SuppliedDraftEvidence(
            True, 'verified', inspection=reconcile.DraftInspection(
                DRAFT, story.metadata.title, 'substantial', False, ('Saved',), True,
                DRAFT, 'Editing post', ('Draft', 'Saved', 'Preview', 'Settings'), 'none',
            ),
        )),
    )
    title_field = MagicMock()
    title_field.is_editable.return_value = True
    monkeypatch.setattr(editor, 'title_fields', lambda _: MagicMock())
    monkeypatch.setattr(editor, 'unique_visible', lambda _: title_field)
    monkeypatch.setattr(editor, 'title_value', lambda _: story.metadata.title)
    surface = MagicMock()
    surface.inner_text.return_value = BODY_TEXT
    monkeypatch.setattr(body, 'locate_body_surface', lambda _: surface)
    file_input = MagicMock()
    file_input.evaluate.return_value = 0
    select = MagicMock()
    target = image_observe.SocialPreviewTarget(MagicMock(), file_input, select)
    monkeypatch.setattr(image_observe, 'open_social_preview_image', MagicMock(return_value=target))
    monkeypatch.setattr(image_observe, 'install_dom_observer', MagicMock())
    monkeypatch.setattr(image_observe, 'wait_for_manual_upload', MagicMock())
    monkeypatch.setattr(
        image_observe, 'read_dom_observation',
        MagicMock(return_value=image_observe.DomObservation(input_events=1, change_events=1)),
    )

    assert main(['substack-image-observe']) == 0

    verify = reconcile.verify_supplied_draft
    assert verify.call_args.args[:3] == (page, URL, DRAFT)
    file_input.set_input_files.assert_not_called()
    select.click.assert_not_called()
    page.get_by_role('button', name='Continue').click.assert_not_called()
    page.get_by_role('button', name='Publish').click.assert_not_called()
    assert repository.get_publication(story.source_hash, 'substack') == before
    output = capsys.readouterr().out
    assert 'social/post preview image' in output
    assert 'Local image state unchanged: Yes' in output
    assert 'Nothing was published.' in output


def test_read_only_social_preview_command_reports_path_without_mutation(
        tmp_path, monkeypatch, capsys):
    story, repository, before = observation_project(tmp_path, monkeypatch)
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*_, **kwargs):
        assert kwargs == {'headless': False}
        yield context

    monkeypatch.setattr(application, 'persistent_browser', launch)
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        MagicMock(return_value=reconcile.SuppliedDraftEvidence(
            True, 'verified', inspection=reconcile.DraftInspection(
                DRAFT, story.metadata.title, 'substantial', False, ('Saved',), True,
                DRAFT, 'Editing post', ('Draft', 'Saved', 'Preview', 'Settings'), 'none',
            ),
        )),
    )
    inspected = image_observe.SocialPreviewInspection(
        image_observe.SocialPreviewState.PRESENT,
        diagnostics=image_observe.SocialPreviewDiagnostics(
            image_label_count=1, image_input_count=1, select_control_count=1,
            replace_control_count=1, local_save_count=1, local_save_enabled=False,
        ),
    )
    monkeypatch.setattr(image_observe, 'inspect_social_preview_image', MagicMock(return_value=inspected))

    assert main(['substack-social-preview-inspect', '--draft-url', DRAFT]) == 0

    output = capsys.readouterr().out
    assert 'Navigation path identified: Yes' in output
    assert 'Social Preview image state: present' in output
    assert 'one dialog-scoped Save/Done button (disabled)' in output
    assert 'No file was selected.' in output
    assert repository.get_publication(story.source_hash, 'substack') == before
    page.get_by_role('button', name='Save').click.assert_not_called()
    page.get_by_role('button', name='Continue').click.assert_not_called()
    page.get_by_role('button', name='Publish').click.assert_not_called()
