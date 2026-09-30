"""Pure, read-only validation of an inspected Substack final publish dialog."""

from dataclasses import dataclass
from enum import Enum

from .publish_inspect import FinalScreenInspection, PublicationControl


class ValidationStatus(str, Enum):
    REQUIRED_CORRECT = 'required_correct'
    REQUIRED_INCORRECT = 'required_incorrect'
    UNAVAILABLE_CONSISTENT = 'unavailable_consistent'
    INFORMATIONAL = 'informational'
    UNKNOWN_AMBIGUOUS = 'unknown_ambiguous'


@dataclass(frozen=True)
class SettingValidation:
    key: str
    label: str
    desired: str
    observed: str
    status: ValidationStatus
    reason: str


@dataclass(frozen=True)
class FinalPublishValidation:
    dialog_found: bool
    desired_configuration: tuple[tuple[str, str], ...]
    required_controls: tuple[SettingValidation, ...]
    informational_controls: tuple[SettingValidation, ...]
    mismatches: tuple[str, ...]
    ambiguities: tuple[str, ...]
    ready_for_publish: bool
    final_action_visible: bool
    final_action_clicked: bool = False


DESIRED_CONFIGURATION = (
    ('audience', 'Everyone'),
    ('comments', 'Everyone'),
    ('delivery', 'Send via email and the Substack app'),
    ('scheduling', 'Immediate'),
    ('tags', 'None'),
)


def _has_dialog_identity(screen: FinalScreenInspection) -> bool:
    attributes = set(screen.screen_attributes)
    return bool(
        screen.screen_kind == 'dialog'
        and 'role=dialog' in attributes
        and 'data-testid=publish-modal' in attributes
        and any(item.startswith('aria-labelledby=') and item != 'aria-labelledby='
                for item in attributes)
    )


def _matching_controls(
    controls: tuple[PublicationControl, ...], *, group: str, name: str,
    roles: frozenset[str],
) -> list[PublicationControl]:
    return [
        control for control in controls
        if not control.final_action
        and control.group == group
        and control.accessible_name == name
        and control.role in roles
    ]


def _selection_text(control: PublicationControl) -> str:
    state = (
        'selected' if control.selected is True else
        'unselected' if control.selected is False else 'state unknown'
    )
    availability = 'enabled' if control.enabled else 'disabled'
    return f'{control.accessible_name} ({state}, {availability})'


def _selected_setting(
    controls: tuple[PublicationControl, ...], *, key: str, label: str, desired: str,
    group: str, name: str, roles: frozenset[str], expected: bool,
    inspect_siblings: bool = True,
) -> SettingValidation:
    matches = _matching_controls(controls, group=group, name=name, roles=roles)
    if not matches:
        return SettingValidation(
            key, label, desired, 'Unknown', ValidationStatus.UNKNOWN_AMBIGUOUS,
            f'Required {group} control {name!r} was not positively identified.',
        )
    if len(matches) != 1:
        return SettingValidation(
            key, label, desired, f'{len(matches)} matching controls',
            ValidationStatus.UNKNOWN_AMBIGUOUS,
            f'Required {group} control {name!r} was ambiguous.',
        )
    target = matches[0]
    if target.selected is None:
        return SettingValidation(
            key, label, desired, _selection_text(target),
            ValidationStatus.UNKNOWN_AMBIGUOUS,
            'The semantic checked/selected state was not exposed unambiguously.',
        )
    siblings = [
        control for control in controls
        if inspect_siblings and not control.final_action and control.group == group
        and control.role in roles and control is not target
    ]
    if target.selected is expected:
        conflicting = [item for item in siblings if item.selected is expected]
        unknown = [item for item in siblings if item.selected is None]
        if conflicting:
            return SettingValidation(
                key, label, desired,
                '; '.join(_selection_text(item) for item in (target, *conflicting)),
                ValidationStatus.REQUIRED_INCORRECT,
                f'More than one {group} choice has the active semantic state.',
            )
        if unknown:
            return SettingValidation(
                key, label, desired,
                '; '.join(_selection_text(item) for item in (target, *unknown)),
                ValidationStatus.UNKNOWN_AMBIGUOUS,
                f'Another {group} choice has an unknown semantic state.',
            )
        observed = desired
        return SettingValidation(
            key, label, desired, observed, ValidationStatus.REQUIRED_CORRECT,
            'The required semantic selection state is positively verified.',
        )
    observed = name if target.selected else f'{name} is off'
    return SettingValidation(
        key, label, desired, observed, ValidationStatus.REQUIRED_INCORRECT,
        'The required semantic selection state does not match the desired configuration.',
    )


def _tags_setting(controls: tuple[PublicationControl, ...]) -> SettingValidation:
    matches = _matching_controls(
        controls, group='Tags', name='Select or create tags', roles=frozenset({'combobox'}),
    )
    if not matches:
        return SettingValidation(
            'tags', 'Tags', 'None', 'Unknown', ValidationStatus.UNKNOWN_AMBIGUOUS,
            'The tags combobox was not positively identified.',
        )
    if len(matches) != 1:
        return SettingValidation(
            'tags', 'Tags', 'None', f'{len(matches)} matching controls',
            ValidationStatus.UNKNOWN_AMBIGUOUS, 'The tags combobox was ambiguous.',
        )
    control = matches[0]
    if control.value not in {'', 'None'}:
        return SettingValidation(
            'tags', 'Tags', 'None', control.value, ValidationStatus.REQUIRED_INCORRECT,
            'The tags control has a non-empty semantic value.',
        )
    status = (
        ValidationStatus.REQUIRED_CORRECT if control.enabled
        else ValidationStatus.UNAVAILABLE_CONSISTENT
    )
    reason = (
        'The tags control has an empty semantic value.' if control.enabled else
        'The tags control is disabled and has an empty semantic value, consistent with no tags.'
    )
    return SettingValidation('tags', 'Tags', 'None', 'None', status, reason)


def _scheduling_setting(controls: tuple[PublicationControl, ...]) -> SettingValidation:
    name = 'Schedule time to email and publish'
    matches = [
        control for control in controls
        if not control.final_action and control.accessible_name == name
    ]
    if len(matches) != 1:
        observed = 'Unknown' if not matches else f'{len(matches)} matching controls'
        return SettingValidation(
            'scheduling', 'Scheduling', 'Immediate', observed,
            ValidationStatus.UNKNOWN_AMBIGUOUS,
            'The exact scheduling control was not uniquely identified.',
        )
    control = matches[0]
    required_attributes = {
        'data-testid=scheduled-at', 'type=button', 'role=checkbox',
    }
    if (
        control.role != 'checkbox'
        or control.control_type != 'button'
        or control.group != 'Delivery'
        or not required_attributes.issubset(control.semantic_attributes)
    ):
        return SettingValidation(
            'scheduling', 'Scheduling', 'Immediate', _selection_text(control),
            ValidationStatus.UNKNOWN_AMBIGUOUS,
            'The scheduling control did not match the observed stable DOM identity.',
        )
    if control.selected is None:
        return SettingValidation(
            'scheduling', 'Scheduling', 'Immediate', _selection_text(control),
            ValidationStatus.UNKNOWN_AMBIGUOUS,
            'The semantic aria-checked state was not exposed unambiguously.',
        )
    if control.selected:
        return SettingValidation(
            'scheduling', 'Scheduling', 'Immediate', name,
            ValidationStatus.REQUIRED_INCORRECT,
            'The scheduling checkbox has aria-checked=true.',
        )
    if 'aria-checked=false' not in control.semantic_attributes:
        return SettingValidation(
            'scheduling', 'Scheduling', 'Immediate', _selection_text(control),
            ValidationStatus.UNKNOWN_AMBIGUOUS,
            'The scheduling checkbox did not expose stable aria-checked=false evidence.',
        )
    return SettingValidation(
        'scheduling', 'Scheduling', 'Immediate', 'Immediate',
        ValidationStatus.REQUIRED_CORRECT,
        'The unique scheduling button has role=checkbox and aria-checked=false.',
    )


def _informational_controls(
    controls: tuple[PublicationControl, ...],
) -> tuple[SettingValidation, ...]:
    ai = []
    for key, name in (
        ('ai_scan', 'Scan for AI text'),
        ('ai_detection', 'Disable AI detection'),
    ):
        matches = [item for item in controls if item.accessible_name == name]
        if len(matches) == 1:
            control = matches[0]
            state = (
                'selected' if control.selected is True else
                'unselected' if control.selected is False else
                control.value if control.value not in {'', 'None'} else 'present; state unknown'
            )
            observed = f'{state}; {"enabled" if control.enabled else "disabled"}'
            reason = 'Observed only; AI control state is not enforced in this validator version.'
        elif matches:
            observed = f'{len(matches)} matching controls; ambiguous'
            reason = 'Ambiguous AI state is informational and is not enforced.'
        else:
            observed = 'Not detected; state unknown'
            reason = 'Missing AI state is informational and is not enforced.'
        ai.append(SettingValidation(
            key, name, 'Not enforced', observed, ValidationStatus.INFORMATIONAL, reason,
        ))

    social_matches = [
        item for item in controls
        if item.group == 'Social preview' or 'Social preview' in item.accessible_name
    ]
    social_observed = 'Present' if social_matches else 'Not reliably detected'
    social_reason = (
        'The social preview section/control was detected; its content is not enforced.'
        if social_matches else
        'Social preview presence could not be determined; it remains informational.'
    )
    return (*ai, SettingValidation(
        'social_preview', 'Social preview', 'Presence observed only', social_observed,
        ValidationStatus.INFORMATIONAL, social_reason,
    ))


def validate_final_publish_configuration(
    screen: FinalScreenInspection,
) -> FinalPublishValidation:
    """Validate a captured screen without browser or persistence access."""
    controls = screen.controls
    required = (
        _selected_setting(
            controls, key='audience', label='Audience', desired='Everyone',
            group='Audience', name='Everyone', roles=frozenset({'radio'}), expected=True,
        ),
        _selected_setting(
            controls, key='comments', label='Comments', desired='Everyone',
            group='Comments', name='Everyone', roles=frozenset({'radio'}), expected=True,
        ),
        _selected_setting(
            controls, key='delivery', label='Delivery',
            desired='Send via email and the Substack app', group='Delivery',
            name='Send via email and the Substack app',
            roles=frozenset({'checkbox', 'radio', 'switch'}), expected=True,
        ),
        _scheduling_setting(controls),
        _tags_setting(controls),
    )
    dialog_found = _has_dialog_identity(screen)
    mismatches = tuple(
        f'{item.label}: {item.reason}' for item in required
        if item.status is ValidationStatus.REQUIRED_INCORRECT
    )
    ambiguities = tuple(
        f'{item.label}: {item.reason}' for item in required
        if item.status is ValidationStatus.UNKNOWN_AMBIGUOUS
    )
    if not dialog_found:
        ambiguities = (
            'Dialog: expected role=dialog, data-testid=publish-modal, and non-empty '
            'aria-labelledby evidence was not positively verified.',
            *ambiguities,
        )
    accepted = {
        ValidationStatus.REQUIRED_CORRECT,
        ValidationStatus.UNAVAILABLE_CONSISTENT,
    }
    ready = dialog_found and all(item.status in accepted for item in required)
    final_action_visible = any(
        item.final_action and item.accessible_name == 'Send to everyone now'
        for item in controls
    )
    return FinalPublishValidation(
        dialog_found, DESIRED_CONFIGURATION, required, _informational_controls(controls),
        mismatches, ambiguities, ready, final_action_visible,
    )
