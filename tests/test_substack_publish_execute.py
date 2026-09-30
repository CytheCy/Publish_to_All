from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from publish_to_all.browser import publish_execute
from publish_to_all.browser.publish_execute import (
    ExecutionStatus, FinalActionTarget, GuardedFinalPublishExecutor,
    PublicationVerification, PublishPreconditions, VerificationStatus,
    validate_final_action, verify_publication,
)
from publish_to_all.browser.publish_inspect import (
    AttributeEvidence, FinalActionCandidate, FinalActionEvidence,
    FinalScreenInspection, PublicationControl,
)
from publish_to_all.state import BodyStatus, ImageStatus, PublicationRepository, PublicationStatus
from publish_to_all.story import load_story


ABSENT = AttributeEvidence(False, '')


def candidate(**changes):
    item = FinalActionCandidate(
        tag_name='button', role='button', accessible_name='Send to everyone now',
        visible_text='Send to everyone now', visible=True, enabled=True,
        effective_type='button', element_type=AttributeEvidence(True, 'button'),
        id=ABSENT, class_name=ABSENT, data_testid=ABSENT, name=ABSENT, value=ABSENT,
        href=ABSENT, target=ABSENT, rel=ABSENT, form_attribute=ABSENT,
        form_owner=None, form_owner_action=ABSENT, form_owner_method=ABSENT,
        form_owner_effective_method=None, nearest_form=None, nearest_form_action=ABSENT,
        nearest_form_method=ABSENT, nearest_form_effective_method=None,
        formaction=ABSENT, formmethod=ABSENT, aria_labelledby=ABSENT,
        aria_describedby=ABSENT, tabindex=ABSENT,
        nearest_dialog='div role="dialog" data-testid="publish-modal"',
        nearest_stable_testid_ancestor='div role="dialog" data-testid="publish-modal"',
        publish_modal_ancestry='indirect', publish_modal_ancestor_count=1,
        belongs_to_exactly_one_publish_modal=True,
    )
    return replace(item, **changes)


def action_evidence(item=None, **changes):
    item = item or candidate()
    evidence = FinalActionEvidence(
        'Send to everyone now', (item,), 1, 1, 0, 1, 0, 1, False, False,
    )
    return replace(evidence, **changes)


def control(name, group, *, role='radio', selected=True, value='', enabled=True,
            final_action=False, control_type='button', attributes=()):
    return PublicationControl(
        name, role, control_type, value, False, not final_action, final_action,
        name, enabled, selected, group, attributes,
    )


def valid_screen(evidence=None):
    controls = (
        control('Everyone', 'Audience'),
        control('Paid subscribers only', 'Audience', selected=False),
        control('Everyone', 'Comments'),
        control('No one', 'Comments', selected=False),
        control('Send via email and the Substack app', 'Delivery', role='checkbox'),
        control(
            'Schedule time to email and publish', 'Delivery', role='checkbox', selected=False,
            attributes=('data-testid=scheduled-at', 'type=button', 'role=checkbox',
                        'aria-checked=false'),
        ),
        control('Select or create tags', 'Tags', role='combobox', selected=None, value=''),
        control('Send to everyone now', 'Actions', final_action=True),
    )
    return FinalScreenInspection(
        'https://example.substack.com/publish/post/123', 'Publish', controls,
        ('Send to everyone now',), None, 'Browser remains open.', (), 'dialog',
        ('scope=dialog', 'role=dialog', 'data-testid=publish-modal',
         'aria-labelledby=publish-modal-title'),
        final_action_evidence=evidence or action_evidence(),
    )


@pytest.mark.parametrize(('change', 'message'), [
    ({'tag_name': 'a'}, 'native button'),
    ({'element_type': ABSENT}, 'explicit type'),
    ({'href': AttributeEvidence(True, '')}, 'href'),
    ({'target': AttributeEvidence(True, '_blank')}, 'target'),
    ({'rel': AttributeEvidence(True, 'noopener')}, 'rel'),
    ({'form_attribute': AttributeEvidence(True, '')}, 'form attribute'),
    ({'form_owner': 'form id="publish"'}, 'form owner'),
    ({'nearest_form': 'form'}, 'nearest form'),
    ({'formaction': AttributeEvidence(True, '')}, 'formaction'),
    ({'formmethod': AttributeEvidence(True, 'post')}, 'formmethod'),
    ({'publish_modal_ancestor_count': 0}, 'exactly one verified'),
])
def test_final_action_guard_fails_closed_for_every_candidate_semantic(change, message):
    result = validate_final_action(action_evidence(candidate(**change)))
    assert not result.allowed
    assert message in ' '.join(result.failures)


@pytest.mark.parametrize('change', [
    {'total_matches': 2}, {'visible_matches': 0}, {'hidden_matches': 1},
    {'enabled_visible_matches': 0}, {'disabled_visible_matches': 1},
    {'publish_modal_matches': 2}, {'multiple_publish_modals': True}, {'ambiguity': True},
])
def test_final_action_guard_fails_closed_for_count_or_ambiguity_change(change):
    assert not validate_final_action(action_evidence(**change)).allowed


def prepared(tmp_path):
    source = tmp_path / 'In' / 'story.md'
    source.parent.mkdir()
    source.write_text('---\ntitle: Story\n---\n' + ('Body text. ' * 200))
    story = load_story(source.parent)
    repository = PublicationRepository(tmp_path / 'state.sqlite3')
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(
        attempt.id, 'https://example.substack.com/publish/post/123',
    )
    inserting = repository.mark_body_inserting(draft)
    body = repository.mark_body_inserted(inserting.id)
    uploading = repository.mark_image_uploading(body)
    record = repository.mark_image_uploaded(uploading)
    assert record.body_status is BodyStatus.INSERTED
    assert record.image_status is ImageStatus.UPLOADED
    return repository, record


def make_executor(monkeypatch, tmp_path, *, screen=None, verifier=None):
    repository, record = prepared(tmp_path)
    page = MagicMock()
    page.url = record.draft_url
    initial = screen or valid_screen()
    handle = MagicMock()
    target = FinalActionTarget(initial.final_action_evidence,
                               initial.final_action_evidence.candidates[0], handle)
    monkeypatch.setattr(publish_execute, 'collect_final_action_target', lambda _page: target)
    executor = GuardedFinalPublishExecutor(
        repository, page, 'https://example.substack.com', record, record.draft_url,
        lambda: initial, verifier=verifier or (lambda *_: PublicationVerification(
            VerificationStatus.PUBLISHED, 'https://example.substack.com/p/story',
            ('public_url=https://example.substack.com/p/story', 'explicit_status=Published'),
            None,
        )),
    )
    return repository, record, page, handle, initial, executor


def test_executor_clicks_pinned_candidate_once_and_persists_positive_verification(
        monkeypatch, tmp_path):
    repository, record, _page, handle, screen, executor = make_executor(
        monkeypatch, tmp_path,
    )
    result = executor.execute(PublishPreconditions(True, True, True), screen)

    assert result.status is ExecutionStatus.PUBLISHED
    assert result.final_click_attempted and executor.final_click_attempted
    handle.click.assert_called_once_with()
    stored = repository.get_publication('' if False else load_story(tmp_path / 'In').source_hash,
                                        'substack')
    assert stored.status is PublicationStatus.PUBLISHED
    assert stored.final_click_attempted
    assert stored.publication_verification_status == 'verified'
    assert stored.published_url == 'https://example.substack.com/p/story'

    second = executor.execute(PublishPreconditions(True, True, True), screen)
    assert second.status is ExecutionStatus.BLOCKED
    handle.click.assert_called_once_with()


def test_changed_preclick_snapshot_blocks_without_persistence_or_click(monkeypatch, tmp_path):
    repository, record, page, handle, screen, executor = make_executor(monkeypatch, tmp_path)
    executor.inspect_screen = lambda: replace(screen, title='Changed')

    result = executor.execute(PublishPreconditions(True, True, True), screen)

    assert result.status is ExecutionStatus.BLOCKED
    assert not result.final_click_attempted
    handle.click.assert_not_called()
    assert repository.get_publication(load_story(tmp_path / 'In').source_hash,
                                      'substack') == record


def test_click_exception_is_uncertain_and_never_retried(monkeypatch, tmp_path):
    repository, _record, _page, handle, screen, executor = make_executor(monkeypatch, tmp_path)
    handle.click.side_effect = PlaywrightLikeError('timeout after dispatch')

    first = executor.execute(PublishPreconditions(True, True, True), screen)
    second = executor.execute(PublishPreconditions(True, True, True), screen)

    assert first.status is ExecutionStatus.UNCERTAIN
    assert second.status is ExecutionStatus.BLOCKED
    handle.click.assert_called_once_with()
    stored = repository.get_publication(load_story(tmp_path / 'In').source_hash, 'substack')
    assert stored.status is PublicationStatus.FAILED
    assert stored.needs_reconciliation and stored.final_click_attempted
    assert stored.publication_verification_status == 'ambiguous'


class PlaywrightLikeError(Exception):
    pass


def test_click_success_without_positive_remote_proof_is_uncertain(monkeypatch, tmp_path):
    ambiguous = PublicationVerification(
        VerificationStatus.AMBIGUOUS, None, ('publish_modal_count=0',),
        'No explicit published state was found.',
    )
    repository, _record, _page, handle, screen, executor = make_executor(
        monkeypatch, tmp_path, verifier=lambda *_: ambiguous,
    )

    result = executor.execute(PublishPreconditions(True, True, True), screen)

    assert result.status is ExecutionStatus.UNCERTAIN
    handle.click.assert_called_once_with()
    stored = repository.get_publication(load_story(tmp_path / 'In').source_hash, 'substack')
    assert stored.status is PublicationStatus.FAILED and stored.needs_reconciliation


def test_post_click_verifier_rejects_modal_disappearance_and_generic_url_change():
    page = MagicMock()
    page.url = 'https://example.substack.com/publish/home'
    page.evaluate.return_value = {
        'statuses': (), 'canonicals': (), 'publishModalCount': 0,
    }
    result = verify_publication(page, 'https://example.substack.com', timeout=0)
    assert result.status is VerificationStatus.AMBIGUOUS
    assert result.published_url is None


def test_uncertain_attempt_can_be_reconciled_only_to_positive_published_state(
        monkeypatch, tmp_path):
    ambiguous = PublicationVerification(
        VerificationStatus.AMBIGUOUS, None, (), 'Remote state could not be read.',
    )
    repository, _record, _page, _handle, screen, executor = make_executor(
        monkeypatch, tmp_path, verifier=lambda *_: ambiguous,
    )
    result = executor.execute(PublishPreconditions(True, True, True), screen)
    reconciled = repository.reconcile_publication_verified(
        result.publication, 'https://example.substack.com/p/story',
        ('public_url=https://example.substack.com/p/story', 'explicit_status=Published'),
    )
    assert reconciled.status is PublicationStatus.PUBLISHED
    assert reconciled.final_click_attempted
    assert not reconciled.needs_reconciliation
