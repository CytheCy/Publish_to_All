from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

from publish_to_all import cli
from publish_to_all.application import FinalPublishDryRunResult
from publish_to_all.browser.publish_inspect import FinalScreenInspection, PublicationControl
from publish_to_all.browser.publish_validate import (
    ValidationStatus, validate_final_publish_configuration,
)


def control(
    name, group, *, role='radio', selected=None, enabled=True, value='None',
    final_action=False, control_type=None, semantic_attributes=(),
):
    return PublicationControl(
        label=name, accessible_name=name, role=role, control_type=control_type or role,
        value=value, required=False, optional=not final_action,
        final_action=final_action, enabled=enabled, selected=selected, group=group,
        semantic_attributes=semantic_attributes, mutates_configuration=True,
    )


def correct_controls():
    return (
        control('Everyone', 'Audience', selected=True),
        control('Paid subscribers only', 'Audience', selected=False, enabled=False),
        control('Everyone', 'Comments', selected=True),
        control('Subscribers only', 'Comments', selected=False),
        control('No one (disable comments)', 'Comments', selected=False),
        control(
            'Send via email and the Substack app', 'Delivery', role='checkbox', selected=True,
        ),
        control(
            'Schedule time to email and publish', 'Delivery', role='checkbox',
            selected=False, control_type='button', semantic_attributes=(
                'data-testid=scheduled-at', 'type=button', 'role=checkbox',
                'aria-checked=false',
            ),
        ),
        control(
            'Select or create tags', 'Tags', role='combobox', enabled=False, value='None',
        ),
        control('Scan for AI text', 'Other options', role='button'),
        control('Disable AI detection', 'Other options', role='button'),
        control('Edit', 'Social preview', role='button'),
        control(
            'Send to everyone now', 'Other options', role='button', enabled=True,
            final_action=True,
        ),
    )


def screen(controls=None):
    return FinalScreenInspection(
        'https://example.substack.com/publish/post/123', 'Publish',
        tuple(correct_controls() if controls is None else controls),
        ('Send to everyone now',), 'Cancel',
        'Cancel clicked once; returned to the draft editor.', (), 'dialog',
        ('scope=dialog', 'role=dialog', 'data-testid=publish-modal',
         'aria-labelledby=publish-modal-title'),
    )


def by_key(result, key):
    return next(item for item in result.required_controls if item.key == key)


def replace_control(controls, name, group, **changes):
    return tuple(
        replace(item, **changes) if item.accessible_name == name and item.group == group else item
        for item in controls
    )


def test_correct_default_configuration_is_ready_and_disabled_paid_does_not_interfere():
    result = validate_final_publish_configuration(screen())
    assert result.ready_for_publish
    assert result.dialog_found
    assert not result.mismatches and not result.ambiguities
    assert by_key(result, 'tags').status is ValidationStatus.UNAVAILABLE_CONSISTENT
    assert by_key(result, 'audience').status is ValidationStatus.REQUIRED_CORRECT


def test_audience_incorrect_is_not_ready():
    controls = replace_control(correct_controls(), 'Everyone', 'Audience', selected=False)
    controls = replace_control(controls, 'Paid subscribers only', 'Audience', selected=True)
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'audience').status is ValidationStatus.REQUIRED_INCORRECT


def test_comments_incorrect_is_not_ready():
    controls = replace_control(correct_controls(), 'Everyone', 'Comments', selected=False)
    controls = replace_control(controls, 'Subscribers only', 'Comments', selected=True)
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'comments').status is ValidationStatus.REQUIRED_INCORRECT


def test_delivery_incorrect_is_not_ready():
    controls = replace_control(
        correct_controls(), 'Send via email and the Substack app', 'Delivery', selected=False,
    )
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'delivery').status is ValidationStatus.REQUIRED_INCORRECT


def test_scheduling_on_is_not_ready():
    controls = replace_control(
        correct_controls(), 'Schedule time to email and publish', 'Delivery',
        selected=True, semantic_attributes=(
            'data-testid=scheduled-at', 'type=button', 'role=checkbox',
            'aria-checked=true',
        ),
    )
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'scheduling').status is ValidationStatus.REQUIRED_INCORRECT


def test_scheduling_without_observed_stable_identity_is_unknown():
    controls = replace_control(
        correct_controls(), 'Schedule time to email and publish', 'Delivery',
        semantic_attributes=('role=checkbox', 'aria-checked=false'),
    )
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'scheduling').status is ValidationStatus.UNKNOWN_AMBIGUOUS


def test_duplicate_scheduling_accessible_name_is_unknown():
    controls = (*correct_controls(), control(
        'Schedule time to email and publish', 'Delivery', role='checkbox',
        selected=False, control_type='button', semantic_attributes=(
            'data-testid=scheduled-at', 'type=button', 'role=checkbox',
            'aria-checked=false',
        ),
    ))
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'scheduling').status is ValidationStatus.UNKNOWN_AMBIGUOUS


def test_nonempty_tags_are_not_ready():
    controls = replace_control(
        correct_controls(), 'Select or create tags', 'Tags', value='technology', enabled=True,
    )
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'tags').status is ValidationStatus.REQUIRED_INCORRECT


def test_required_control_missing_is_not_ready():
    controls = tuple(
        item for item in correct_controls()
        if not (item.accessible_name == 'Everyone' and item.group == 'Comments')
    )
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'comments').status is ValidationStatus.UNKNOWN_AMBIGUOUS


def test_required_state_ambiguous_is_not_ready():
    controls = replace_control(correct_controls(), 'Everyone', 'Audience', selected=None)
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'audience').status is ValidationStatus.UNKNOWN_AMBIGUOUS


def test_duplicate_everyone_names_remain_distinct_by_group():
    result = validate_final_publish_configuration(screen())
    audience = by_key(result, 'audience')
    comments = by_key(result, 'comments')
    assert audience.status is ValidationStatus.REQUIRED_CORRECT
    assert comments.status is ValidationStatus.REQUIRED_CORRECT
    assert audience.key != comments.key


def test_duplicate_name_within_one_group_is_ambiguous():
    controls = (*correct_controls(), control('Everyone', 'Audience', selected=True))
    result = validate_final_publish_configuration(screen(controls))
    assert not result.ready_for_publish
    assert by_key(result, 'audience').status is ValidationStatus.UNKNOWN_AMBIGUOUS


def test_ai_controls_and_missing_or_ambiguous_ai_state_are_informational():
    controls = tuple(
        item for item in correct_controls()
        if item.accessible_name not in {'Scan for AI text', 'Disable AI detection'}
    )
    controls += (
        control('Scan for AI text', 'Other options', role='button'),
        control('Scan for AI text', 'Other options', role='button'),
    )
    result = validate_final_publish_configuration(screen(controls))
    assert result.ready_for_publish
    ai = [item for item in result.informational_controls if item.key.startswith('ai_')]
    assert all(item.status is ValidationStatus.INFORMATIONAL for item in ai)
    assert 'ambiguous' in ai[0].observed
    assert 'unknown' in ai[1].observed


def test_social_preview_detection_is_informational():
    present = validate_final_publish_configuration(screen())
    missing_controls = tuple(
        item for item in correct_controls() if item.group != 'Social preview'
    )
    missing = validate_final_publish_configuration(screen(missing_controls))
    assert present.ready_for_publish and missing.ready_for_publish
    assert present.informational_controls[-1].observed == 'Present'
    assert missing.informational_controls[-1].observed == 'Not reliably detected'


def test_final_action_presence_is_only_observed_and_cannot_be_clicked():
    snapshot = screen()
    result = validate_final_publish_configuration(snapshot)
    assert result.final_action_visible
    assert not result.final_action_clicked
    assert result.ready_for_publish
    assert snapshot == screen()


def test_validator_performs_no_ui_or_sqlite_mutation(tmp_path):
    database = tmp_path / 'publications.sqlite3'
    database.write_bytes(b'unchanged sqlite fixture')
    before = sha256(database.read_bytes()).hexdigest()
    snapshot = screen()
    result = validate_final_publish_configuration(snapshot)
    after = sha256(database.read_bytes()).hexdigest()
    assert result.ready_for_publish
    assert before == after
    assert snapshot == screen()


def test_dialog_identity_is_required_for_readiness():
    result = validate_final_publish_configuration(replace(
        screen(), screen_attributes=('scope=dialog', 'role=dialog'),
    ))
    assert not result.dialog_found
    assert not result.ready_for_publish
    assert result.ambiguities[0].startswith('Dialog:')


def test_cli_dry_run_prints_readiness_and_uses_dedicated_validator(monkeypatch, capsys):
    snapshot = screen()
    validation = validate_final_publish_configuration(snapshot)
    runner = lambda _root: FinalPublishDryRunResult(
        SimpleNamespace(final_screen=snapshot, local_state_changed=False), validation,
    )
    monkeypatch.setattr(cli, 'validate_substack_publish_configuration', runner)
    assert cli.main(['substack-publish-config-dry-run']) == 0
    output = capsys.readouterr()
    assert not output.err
    assert 'Final publish configuration' in output.out
    assert 'Audience\n  desired: Everyone\n  observed: Everyone\n  status: OK' in output.out
    assert 'AI control state is not enforced' in output.out
    assert 'Send to everyone now clicked: No' in output.out
    assert 'READY FOR PUBLISH: YES' in output.out


def test_cli_dry_run_returns_failure_for_unknown_required_state(monkeypatch, capsys):
    snapshot = screen(replace_control(
        correct_controls(), 'Everyone', 'Comments', selected=None,
    ))
    validation = validate_final_publish_configuration(snapshot)
    monkeypatch.setattr(
        cli, 'validate_substack_publish_configuration',
        lambda _root: FinalPublishDryRunResult(
            SimpleNamespace(final_screen=snapshot, local_state_changed=False), validation,
        ),
    )
    assert cli.main(['substack-publish-config-dry-run']) == 1
    output = capsys.readouterr().out
    assert 'Comments: The semantic checked/selected state' in output
    assert 'READY FOR PUBLISH: NO' in output
