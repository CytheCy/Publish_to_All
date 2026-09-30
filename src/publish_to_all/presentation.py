"""Human-readable reports, without terminal or filesystem side effects."""

import json
import re

from .application import (
    DeletedDraftCleanupResult, FinalPublishDryRunResult, ImageObservationResult,
    ImageReconciliationResult, Inspection,
    PublishContinueDiagnosticResult, PublishInspectionResult, SocialPreviewReadOnlyResult,
    StoryStatus,
)
from .browser.publish_validate import ValidationStatus
from .browser.publish_inspect import AttributeEvidence
from .state import BodyStatus, ImageStatus, PublicationReassociation, PublicationStatus
from .browser.substack import AuthenticationState, SessionResult


def format_substack_session(result: SessionResult) -> str:
    labels = {
        AuthenticationState.AUTHENTICATED: "Authenticated",
        AuthenticationState.NOT_AUTHENTICATED: "Not authenticated",
        AuthenticationState.UNKNOWN: "Unknown (could not verify authentication)",
        AuthenticationState.RATE_LIMITED: "Temporarily rate limited",
    }
    lines = ["Substack session", "", "Publication:", result.publication_url, "",
             f"Authentication: {labels[result.authentication]}", "",
             "Browser profile:", str(result.profile)]
    if result.authentication == AuthenticationState.AUTHENTICATED:
        lines += ["", "A reusable authenticated browser session was detected."]
    elif result.authentication == AuthenticationState.RATE_LIMITED:
        lines += ["", "Substack is refusing requests from this browser session right now.", "",
                  "Retry manually later with:", "", "publish-to-all substack-session"]
    else:
        lines += ["", "Run:", "", "publish-to-all substack-login", "",
                  "to authenticate manually and check again."]
    if result.diagnostics is not None:
        diagnostic = result.diagnostics
        lines += ["", "Final URL (sensitive components redacted):", diagnostic.final_url,
                  "", "Page title:", diagnostic.title]
        if diagnostic.rate_limited:
            lines += ["", "Rate limiting detected:", "Yes"]
        lines += ["", "Visible controls:",
                  *[f"- {label}" for label in diagnostic.controls]]
        if not diagnostic.controls:
            lines += ["- No recognized controls"]
        lines += ["", "Diagnostic screenshot (text and images redacted):",
                  str(diagnostic.screenshot) if diagnostic.screenshot else "Unavailable (capture failed)"]
    return "\n".join([*lines, "", "No local publication state was changed.",
                      "No draft was created.", "Nothing was published."])


def _warnings(result: Inspection) -> list[str]:
    return [f"Warning: {warning}" for warning in result.story.warnings]


def _substack_image_lines(story) -> list[str]:
    name = story.substack_image.name if story.substack_image else 'none'
    lines = [f"Substack social preview image: {name}"]
    if story.substack_image_fallback:
        lines.append("(fallback to matching general story image)")
    if story.substack_image_info:
        info = story.substack_image_info
        lines.append(
            f"Substack image details: {info.width}x{info.height}, "
            f"{info.mime_type}, {info.size_bytes:,} bytes"
        )
    return lines


def format_check(result: Inspection) -> str:
    story = result.story
    status = " with warnings" if story.warnings else ""
    config_status = "OK" if result.config.publication_url else "Not configured (offline check)"
    lines = [
        f"Story check passed{status}", "",
        f"Story: {story.source.name}", f"Title: {story.metadata.title}",
        f"Image: {story.image.name if story.image else 'none'}",
        *_substack_image_lines(story),
        f"Words: {story.word_count}", f"Content hash: {story.source_hash}", "",
        f"Substack configuration: {config_status}",
    ]
    if story.warnings:
        lines += ["", *_warnings(result)]
    return "\n".join([*lines, "", "No files were modified.", "Nothing was published."])


def format_preview(result: Inspection) -> str:
    story = result.story
    metadata = story.metadata
    lines = ["Story detected", "", f"File: {story.source.name}", f"Title: {metadata.title}"]
    for field in ("subtitle", "description", "series", "episode", "tags"):
        value = getattr(metadata, field)
        if value is not None and value != ():
            value = ", ".join(value) if field == "tags" else value
            lines.append(f"{field.capitalize()}: {value}")
    lines += [f"Words: {story.word_count}", f"Image: {story.image.name if story.image else 'none'}",
              *_substack_image_lines(story),
              "", "Destination", "", "Substack"]
    if result.config.publication_url:
        lines += [f"Publication: {result.config.publication_url}", "Status: Ready (local validation only)"]
    else:
        lines += ["Status: Not configured (offline preview)"]
    lines += ["", "Content hash:", story.source_hash]
    if story.warnings:
        lines += ["", *_warnings(result)]
    return "\n".join([*lines, "", "Nothing was published."])


STATUS_LABELS = {
    PublicationStatus.NOT_STARTED: "Not started",
    PublicationStatus.DRAFT_CREATING: "Draft creation in progress (reconciliation required before retry)",
    PublicationStatus.DRAFT_CREATED: "Draft created",
    PublicationStatus.PUBLISHING: "Publishing in progress (reconciliation required before retry)",
    PublicationStatus.PUBLISHED: "Published",
    PublicationStatus.FAILED: "Failed",
}


def format_substack_draft(result: StoryStatus) -> str:
    return "\n".join([
        "Substack draft created", "", "Story:", result.story.metadata.title, "",
        "Added:", "Title", "", "Not added yet:", "Story body", "Image", "",
        "Draft URL:", result.substack.draft_url or "Not available", "",
        "Status:", "Draft created", "", "Nothing was published.",
    ])


def format_substack_body(result: StoryStatus) -> str:
    return "\n".join([
        "Substack draft body added", "", "Story:", result.story.metadata.title, "",
        "Draft:", result.substack.draft_url, "", "Added:", "Story body", "",
        "Not added yet:", "Cover image", "", "Status:", "Body inserted", "",
        "Nothing was published.",
    ])


def format_substack_image(result: StoryStatus) -> str:
    return "\n".join([
        "Substack Social Preview image uploaded", "", "Story:", result.story.metadata.title, "",
        "Draft:", result.substack.draft_url, "", "Image:", result.story.substack_image.name, "",
        "Title:", "Preserved", "", "Body:", "Preserved", "", "Status:",
        "Image uploaded", "", "Nothing was published.",
    ])


def format_substack_image_reconciliation(result: ImageReconciliationResult) -> str:
    retry = result.remote_social_preview_state == 'none'
    return "\n".join([
        "Substack image attempt reconciled", "", "Story:", result.story.metadata.title, "",
        "Story hash:", result.story.source_hash, "", "Draft:", result.substack.draft_url, "",
        "Remote Social Preview image:", result.remote_social_preview_state, "", "Local image state:",
        result.substack.image_status.value, "", "Image upload retry:",
        "Permitted by local state" if retry else "Blocked because the Social Preview image is present", "",
        "Remote draft was not modified.", "No image was uploaded.", "Nothing was published.",
    ])


def format_substack_image_observation(result: ImageObservationResult) -> str:
    dom = result.dom
    yes = lambda value: 'Yes' if value else 'No'
    return "\n".join([
        "Substack manual image observation complete", "", "Story:",
        result.story.metadata.title, "", "Draft:", result.draft_url, "",
        "Observed feature:", result.target_feature, "",
        "Manual browser evidence:",
        f"Input events: {dom.input_events}",
        f"Change events: {dom.change_events}",
        f"Original input received a file: {yes(dom.original_input_received_file)}",
        f"Different input received a file: {yes(dom.different_input_received_file)}",
        f"Different file input appeared: {yes(dom.different_input_appeared)}",
        f"Original file input replaced: {yes(dom.original_input_replaced)}",
        f"DOM nodes added: {dom.added_nodes}",
        f"DOM nodes removed: {dom.removed_nodes}",
        f"Processing indicator appeared: {yes(dom.processing_appeared)}",
        f"Preview appeared: {yes(dom.preview_appeared)}",
        f"Social preview dialog rerendered or closed: {yes(dom.social_preview_rerendered)}",
        f"Active control changed: {yes(dom.active_control_changed)}",
        f"Fresh Saving signal appeared: {yes(dom.saving_appeared)}",
        f"Fresh Saved signal appeared: {yes(dom.saved_appeared)}",
        f"Final file-input count: {dom.observed_input_count}", "",
        "Safe network evidence:",
        f"Trusted image/fetch/XHR responses: {result.network_responses}",
        f"Successful responses: {result.successful_network_responses}",
        f"Failed responses: {result.failed_network_responses}", "",
        f"Draft URL unchanged: {yes(result.final_url_unchanged)}",
        f"Title preserved: {yes(result.title_preserved)}",
        f"Body preserved: {yes(result.body_preserved)}",
        f"Local image state unchanged: {yes(result.local_state_unchanged)}", "",
        "No file was selected by this command.",
        "No publication control was clicked by this command.",
        "Nothing was published.",
    ])


def format_social_preview_inspection(result: SocialPreviewReadOnlyResult) -> str:
    enabled = 'enabled' if result.local_save_enabled else 'disabled'
    local_action = (
        f'one dialog-scoped Save/Done button ({enabled})'
        if result.local_save_count == 1 else
        f'ambiguous ({result.local_save_count} Save/Done buttons)'
    )
    return '\n'.join([
        'Substack Social Preview inspection', '',
        'Draft:', result.draft_url, '',
        'Navigation path identified: Yes',
        'Post settings control: role=button, name=Settings, data-testid=settings-button',
        'Post settings panel: role=dialog, name=Post settings, data-testid=settings-modal',
        'Social preview control: exact label=Social preview with one panel-scoped Edit button',
        'Social Preview editor: role=dialog, name=Edit social preview, data-testid=modal',
        'Image control: input type=file, accept=image/*, aria-label=Upload file',
        'Social Preview image state: ' + result.image_state,
        'Local action: ' + local_action + ' (not clicked)', '',
        'No file was selected.',
        'No Save, Done, Continue, Publish, Send, or Schedule control was clicked.',
        'No remote or local changes were made.',
        'Nothing was published.',
    ])


def format_substack_publish_inspection(
    result: PublishInspectionResult | PublishContinueDiagnosticResult,
) -> str:
    if isinstance(result, PublishContinueDiagnosticResult):
        diagnostic = result.diagnostic
        lines = [
            'Substack Continue-control read-only diagnostic', '',
            f'Draft: {result.substack.draft_url}',
            'Authenticated editor: Verified',
            'Prepared draft: Verified', '',
            f'Exact accessible-name Continue candidates: {len(diagnostic.candidates)}',
            f'Visible and enabled candidates: {diagnostic.visible_enabled_count}',
        ]
        for index, item in enumerate(diagnostic.candidates, 1):
            nearby = '; '.join(item.nearby_labels) or '[none]'
            lines.extend([
                '', f'Candidate {index}:',
                f'Tag: {item.tag}', f'Role: {item.role}',
                f'Accessible name: {item.accessible_name}',
                f'Visible text: {item.visible_text}',
                f'aria-label: {item.aria_label}', f'title: {item.title}',
                f'data-testid: {item.test_id}', f'type attribute: {item.element_type}',
                f'href: {item.href}',
                f'Visible/enabled: {"Yes" if item.visible else "No"}/'
                f'{"Yes" if item.enabled else "No"}',
                f'Inside dialog: {"Yes" if item.in_dialog else "No"}',
                f'Inside authenticated editor context: {"Yes" if item.in_editor else "No"}',
                f'Appears to submit: {"Yes" if item.appears_submit else "No"}',
                f'Form: {item.form_attributes}',
                f'Ancestor context: {item.ancestor_context}',
                f'Nearby stable labels: {nearby}',
            ])
        lines.extend([
            '', 'Continue clicked: No',
            f'Local state changed: {"Yes" if result.local_state_changed else "No"}',
            'Nothing published: Yes',
        ])
        return '\n'.join(lines)
    screen = result.final_screen
    grouped = {}
    navigation = []
    for control in screen.controls:
        if control.final_action:
            continue
        if control.safe_navigation:
            navigation.append(control)
            continue
        grouped.setdefault(control.group, []).append(control)
    options = []
    for group, controls in grouped.items():
        options.extend([f'{group}:'])
        for control in controls:
            attributes = ', '.join(control.semantic_attributes) or '[none]'
            selected = (
                'Selected' if control.selected is True else
                'Unselected' if control.selected is False else 'Not applicable'
            )
            options.extend([
                f'- Visible label: {control.label}',
                f'  Accessible name: {control.accessible_name}',
                f'  Role/type: {control.role}/{control.control_type}',
                f'  Current value/default: {control.value}',
                f'  Enabled: {"Yes" if control.enabled else "No"}',
                f'  Selection state: {selected}',
                f'  Required/optional: {"Required" if control.required else "Optional"}',
                f'  Stable semantic attributes: {attributes}',
                f'  Mutates publication configuration: '
                f'{"Yes" if control.mutates_configuration else "No"}',
                '  Final action: No',
                f'  Relevant to future automation: '
                f'{"Yes; requires an explicit desired value" if control.mutates_configuration else "No"}',
            ])
    if not options:
        options = ['Other options:', '- No non-final configuration controls were identified.']
    final_actions = []
    for control in screen.controls:
        if not control.final_action:
            continue
        attributes = ', '.join(control.semantic_attributes) or '[none]'
        effect = (
            'schedule publication' if control.label.lower().startswith('schedule') else
            'send and/or publish immediately' if re.search(r'\b(?:now|send)\b', control.label, re.I) else
            'commit publication'
        )
        final_actions.extend([
            f'- {control.label}',
            f'  Role/type: {control.role}/{control.control_type}',
            f'  Enabled: {"Yes" if control.enabled else "No"}',
            f'  Apparent effect: {effect}',
            f'  Stable semantic attributes: {attributes}',
            '  Clicked: No',
        ])
    safe_navigation = [
        f'- {control.label} [{"enabled" if control.enabled else "disabled"}]'
        for control in navigation
    ]
    blocked = ', '.join(screen.blocked_mutation_methods) or 'None'
    evidence = ', '.join(screen.screen_attributes) or '[none]'
    scheduling = screen.scheduling
    scheduling_lines = ['SCHEDULING = UNKNOWN', 'Focused scheduling evidence unavailable.']
    if scheduling is not None:
        enabled = (
            'Yes' if scheduling.enabled is True else
            'No' if scheduling.enabled is False else 'Unknown'
        )
        scheduling_lines = [
            f'SCHEDULING = {scheduling.state}',
            f'Semantic element: {scheduling.semantic_element}',
            f'Role: {scheduling.role}',
            f'Accessible name: {scheduling.accessible_name}',
            f'Checked semantics: {scheduling.checked_semantics}',
            f'Enabled: {enabled}',
            f'Group identity: {scheduling.group_identity}',
            'Stable attributes: ' + (', '.join(scheduling.stable_attributes) or '[none]'),
            f'Exact accessible-name matches: {scheduling.exact_name_match_count}',
            f'Visible exact matches: {scheduling.visible_exact_name_match_count}',
            f'Hidden exact matches: {scheduling.hidden_exact_name_match_count}',
            f'Relationship to delivery: {scheduling.delivery_relationship}',
            'Relevant ancestry:',
            *[f'- {item}' for item in scheduling.ancestry],
            'Related delivery/native controls:',
            *[f'- {item}' for item in scheduling.related_controls],
        ]
    return '\n'.join([
        'Substack final publication-screen inspection', '',
        'Preflight:',
        f'Exact current story hash: {result.story.source_hash}',
        f'Linked numeric draft URL: {result.substack.draft_url}',
        f'Draft state: {result.substack.status.value}',
        f'Body state: {result.substack.body_status.value}',
        f'Social Preview image state: {result.substack.image_status.value} locally; present remotely',
        'Reconciliation required: No',
        'Authenticated editor: Verified',
        'Title match: Yes',
        'Body substantial: Yes',
        'Editor saved: Yes', '',
        'Continue control:',
        f'Label: {result.continue_label}',
        f'Role/type: {result.continue_role}/{result.continue_type}',
        f'data-testid: {result.continue_test_id or "[none]"}',
        f'Context: {result.continue_context}',
        'Clicked: Exactly once',
        'Pre-click interpretation: editor navigation control; not a submit control', '',
        'Final publication screen:',
        f'URL: {screen.url}',
        f'Title: {screen.title}',
        f'Scope: {screen.screen_kind}',
        f'Stable evidence: {evidence}', '',
        'Visible options/defaults:',
        *options, '',
        *scheduling_lines, '',
        'Final action controls:',
        *(final_actions or ['- None positively identified']),
        'Safe navigation controls:',
        *(safe_navigation or ['- None positively identified']), '',
        f'Unexpected mutating network activity: {"Yes" if screen.blocked_mutation_methods else "No"}',
        f'Mutation request methods blocked after Continue: {blocked}', '',
        'Safe exit behavior:',
        screen.exit_behavior,
        f'Safe exit control: {screen.safe_exit or "None used"}', '',
        f'SQLite unchanged: {"No" if result.local_state_changed else "Yes"}',
        f'Local state changed: {"Yes" if result.local_state_changed else "No"}', '',
        'No settings changed.',
        'Nothing published: Yes', '',
        'Recommended next automation stage:',
        'Model the observed configuration controls with explicit desired values and a separate '
        'dry-run validator. Keep the final action behind distinct authorization.',
    ])


def format_final_publish_validation(result: FinalPublishDryRunResult) -> str:
    validation = result.validation
    screen = result.inspection.final_screen
    status_labels = {
        ValidationStatus.REQUIRED_CORRECT: 'OK',
        ValidationStatus.REQUIRED_INCORRECT: 'MISMATCH',
        ValidationStatus.UNAVAILABLE_CONSISTENT: 'OK (disabled/unavailable as expected)',
        ValidationStatus.INFORMATIONAL: 'INFORMATIONAL',
        ValidationStatus.UNKNOWN_AMBIGUOUS: 'UNKNOWN/AMBIGUOUS',
    }
    lines = [
        'Final publish configuration', '',
        f'Publish dialog identity matched: {"Yes" if validation.dialog_found else "No"}',
    ]
    for item in validation.required_controls:
        lines.extend([
            '', item.label,
            f'  desired: {item.desired}',
            f'  observed: {item.observed}',
            f'  status: {status_labels[item.status]}',
            f'  evidence: {item.reason}',
        ])
    lines.extend(['', 'Informational controls'])
    for item in validation.informational_controls:
        lines.extend([
            '', item.label,
            f'  observed: {item.observed}',
            '  enforcement: informational only; does not affect publish readiness',
            f'  evidence: {item.reason}',
        ])
    if validation.mismatches:
        lines.extend(['', 'Mismatches:', *(f'- {item}' for item in validation.mismatches)])
    if validation.ambiguities:
        lines.extend(['', 'Unknown or ambiguous:', *(f'- {item}' for item in validation.ambiguities)])
    lines.extend([
        '', f'Send to everyone now visible: {"Yes" if validation.final_action_visible else "No"}',
        'Send to everyone now clicked: No',
        f'Unexpected mutating network activity: '
        f'{"Yes" if screen.blocked_mutation_methods else "No"}',
        f'SQLite unchanged: {"No" if result.inspection.local_state_changed else "Yes"}',
        f'Browser exit: {screen.exit_behavior}',
        'No controls changed.',
        'Nothing published: Yes', '',
        f'READY FOR PUBLISH: {"YES" if validation.ready_for_publish else "NO"}',
    ])
    return '\n'.join(lines)


def _format_attribute(attribute: AttributeEvidence) -> str:
    return json.dumps(attribute.value) if attribute.present else 'absent'


def format_final_action_diagnostic(result: PublishInspectionResult) -> str:
    """Render each raw final-action candidate without collapsing duplicates."""
    screen = result.final_screen
    evidence = screen.final_action_evidence
    if evidence is None:
        return '\n'.join([
            'Final action evidence', '', 'Evidence unavailable.',
            'Send to everyone now clicked: No', 'Nothing published: Yes',
        ])
    lines = [
        'Final action evidence', '',
        f'Exact accessible name: {evidence.exact_accessible_name}', '',
        'Matches:',
        f'  total: {evidence.total_matches}',
        f'  visible: {evidence.visible_matches}',
        f'  hidden: {evidence.hidden_matches}',
        f'  enabled visible: {evidence.enabled_visible_matches}',
        f'  disabled visible: {evidence.disabled_visible_matches}',
        f'Publish modal matches: {evidence.publish_modal_matches}',
        f'More than one matching publish modal: {"yes" if evidence.multiple_publish_modals else "no"}',
        f'Ambiguous: {"yes" if evidence.ambiguity else "no"}',
    ]
    for index, item in enumerate(evidence.candidates, 1):
        lines.extend([
            '', f'Candidate {index}:',
            f'  native tag: {item.tag_name}',
            f'  role: {item.role}',
            f'  computed accessible name: {item.accessible_name}',
            f'  visible text: {json.dumps(item.visible_text)}',
            f'  visible: {"yes" if item.visible else "no"}',
            f'  enabled: {"yes" if item.enabled else "no"}',
            f'  type attribute: {_format_attribute(item.element_type)}',
            f'  effective type: {item.effective_type}',
            f'  id: {_format_attribute(item.id)}',
            f'  class: {_format_attribute(item.class_name)}',
            f'  data-testid: {_format_attribute(item.data_testid)}',
            f'  name: {_format_attribute(item.name)}',
            f'  value: {_format_attribute(item.value)}',
            f'  href: {_format_attribute(item.href)}',
            f'  target: {_format_attribute(item.target)}',
            f'  rel: {_format_attribute(item.rel)}',
            f'  form attribute: {_format_attribute(item.form_attribute)}',
            f'  form owner: {item.form_owner or "absent"}',
            f'  form owner action: {_format_attribute(item.form_owner_action)}',
            f'  form owner method: {_format_attribute(item.form_owner_method)}',
            f'  form owner effective method: {item.form_owner_effective_method or "absent"}',
            f'  nearest form: {item.nearest_form or "absent"}',
            f'  nearest form action: {_format_attribute(item.nearest_form_action)}',
            f'  nearest form method: {_format_attribute(item.nearest_form_method)}',
            f'  nearest form effective method: {item.nearest_form_effective_method or "absent"}',
            f'  formaction: {_format_attribute(item.formaction)}',
            f'  formmethod: {_format_attribute(item.formmethod)}',
            f'  aria-labelledby: {_format_attribute(item.aria_labelledby)}',
            f'  aria-describedby: {_format_attribute(item.aria_describedby)}',
            f'  tabindex: {_format_attribute(item.tabindex)}',
            f'  nearest dialog: {item.nearest_dialog or "absent"}',
            '  nearest stable data-testid ancestor: '
            f'{item.nearest_stable_testid_ancestor or "absent"}',
            f'  publish-modal ancestry: {item.publish_modal_ancestry}',
            f'  publish-modal ancestor matches: {item.publish_modal_ancestor_count}',
            '  belongs to exactly one verified publish modal: '
            f'{"yes" if item.belongs_to_exactly_one_publish_modal else "no"}',
        ])
    lines.extend([
        '', f'Unexpected mutating network activity: '
        f'{"yes" if screen.blocked_mutation_methods else "no"}',
        'Mutation request methods blocked: '
        f'{", ".join(screen.blocked_mutation_methods) or "none"}',
        f'SQLite SHA-256 before: {result.database_sha256_before}',
        f'SQLite SHA-256 after: {result.database_sha256_after}',
        f'SQLite unchanged: {"no" if result.local_state_changed else "yes"}',
        f'Browser exit method: {screen.exit_behavior}',
        'Final action clicked: No',
        'No controls changed.',
        'Nothing published: Yes',
    ])
    return '\n'.join(lines)


def format_substack_publish_execution(result) -> str:
    """Render the executor's machine result without making workflow decisions."""
    if result.dry_run:
        guard = result.action_guard
        candidate = result.candidate
        lines = [
            'FINAL ACTION VERIFIED' if result.status.value == 'dry_run_verified'
            else 'FINAL ACTION VERIFICATION FAILED',
            'NO CLICK PERFORMED',
            'NOTHING PUBLISHED', '',
            f'Final screen reached: {"Yes" if result.status.value == "dry_run_verified" else "No"}',
            f'Candidate count: {result.candidate_count}',
            f'Uniquely pinned: {"Yes" if candidate is not None and result.candidate_count == 1 else "No"}',
            f'All executor guards passed: {"Yes" if guard and guard.allowed else "No"}',
            f'Pre-click revalidation passed: {"Yes" if result.pre_click_revalidation_passed else "No"}',
            'Playwright click calls: 0',
            f'Unexpected publish/send/schedule network mutation: '
            f'{"Yes" if result.write_like_network_methods else "No"}',
            f'Network methods observed: {", ".join(result.write_like_network_methods) or "None"}',
            f'SQLite checksum before: {result.database_sha256_before}',
            f'SQLite checksum after: {result.database_sha256_after}',
            f'SQLite unchanged: {"Yes" if result.database_sha256_before == result.database_sha256_after else "No"}',
            f'Runtime schema before: {result.schema_version_before}',
            f'Runtime schema after: {result.schema_version_after}',
        ]
        if candidate is not None:
            lines.extend([
                '', 'Final action candidate:',
                f'  tag: {candidate.tag_name}',
                f'  role: {candidate.role}',
                f'  accessible name: {candidate.accessible_name}',
                f'  visible text: {candidate.visible_text}',
                f'  type: {_format_attribute(candidate.element_type)}',
                f'  data-testid: {_format_attribute(candidate.data_testid)}',
                f'  enabled: {"Yes" if candidate.enabled else "No"}',
                f'  visible: {"Yes" if candidate.visible else "No"}',
                f'  dialog ancestry: {candidate.nearest_dialog or "None"}',
                f'  form membership: owner={candidate.form_owner or "None"}; '
                f'nearest={candidate.nearest_form or "None"}',
                f'  href/link state: {_format_attribute(candidate.href)}; '
                f'target={_format_attribute(candidate.target)}; rel={_format_attribute(candidate.rel)}',
                f'  publication-modal ancestry: {candidate.publish_modal_ancestry}',
                f'  publication-modal ancestor count: {candidate.publish_modal_ancestor_count}',
                f'  belongs to exactly one publication modal: '
                f'{"Yes" if candidate.belongs_to_exactly_one_publish_modal else "No"}',
            ])
        if result.failures:
            lines.extend(['', 'Reasons:', *(f'- {item}' for item in result.failures)])
        return '\n'.join(lines)
    lines = [
        'Guarded Substack publication', '',
        f'Status: {result.status.value}',
        f'Final click attempted: {"Yes" if result.final_click_attempted else "No"}',
    ]
    if result.failures:
        lines.extend(['', 'Reasons:', *(f'- {reason}' for reason in result.failures)])
    if result.verification is not None:
        lines.extend([
            '', f'Post-click verification: {result.verification.status.value}',
            f'Published URL: {result.verification.published_url or "Unverified"}',
            'Evidence: ' + (', '.join(result.verification.evidence) or 'None'),
        ])
        if result.verification.ambiguity_reason:
            lines.append(f'Ambiguity: {result.verification.ambiguity_reason}')
    if result.publication is not None:
        lines.extend([
            '', f'Local publication state: {result.publication.status.value}',
            'Reconciliation required: '
            f'{"Yes" if result.publication.needs_reconciliation else "No"}',
        ])
    return '\n'.join(lines)


def format_substack_title(result: StoryStatus) -> str:
    return "\n".join([
        "Substack draft title repaired", "", "Story:", result.story.metadata.title, "",
        "Draft:", result.substack.draft_url, "", "Title:", result.story.metadata.title, "",
        "Body:", "Still empty", "", "Nothing was published.",
    ])


def format_substack_reassociation(result: PublicationReassociation) -> str:
    return "\n".join([
        "Substack draft reassociated to corrected story version", "",
        "Old hash:", result.from_story.source_hash, "",
        "New hash:", result.to_story.source_hash, "",
        "Draft:", result.publication.draft_url, "",
        "Remote draft was not modified.",
        "Nothing was published.",
    ])


def format_deleted_draft_cleanup(result: DeletedDraftCleanupResult) -> str:
    return "\n".join([
        "Obsolete deleted Substack draft removed from local state", "",
        "Historical story:", result.story.title, "",
        "Story hash:", result.story.source_hash, "",
        "Deleted draft:", result.removal.deleted_draft_url, "",
        "Remote deletion verified:", result.remote_reason, "",
        "Publication record removed:", str(result.removal.publication_id), "",
        "Story-version row removed:", "Yes" if result.removal.story_removed else "No", "",
        "Nothing was published.",
    ])


def format_status(result: StoryStatus) -> str:
    story, record = result.story, result.substack
    status = record.status if record else PublicationStatus.NOT_STARTED
    lines = ["Story status", "", f"File: {story.source.name}",
             f"Title: {story.metadata.title}", f"Hash: {story.source_hash}", "",
             f"Substack: {STATUS_LABELS[status]}"]
    if record:
        if record.draft_url:
            lines.append(f"Draft: {record.draft_url}")
        if record.published_url:
            lines.append(f"Published: {record.published_url}")
        if record.error_message:
            lines.append(f"Error: {record.error_message}")
        if record.needs_reconciliation:
            lines.append("Reconciliation required before retrying: the remote outcome is unresolved.")
        if record.body_status != BodyStatus.NOT_STARTED:
            body_labels = {
                BodyStatus.INSERTING: 'Insertion in progress; inspect the draft before retrying',
                BodyStatus.INSERTED: 'Inserted',
                BodyStatus.FAILED: 'Insertion failed; inspect the draft before retrying',
            }
            lines.append(f"Body: {body_labels[record.body_status]}")
        if record.body_error_message:
            lines.append(f"Body error: {record.body_error_message}")
        if record.image_status != ImageStatus.NOT_STARTED:
            image_labels = {
                ImageStatus.UPLOADING: 'Upload in progress; inspect the draft before retrying',
                ImageStatus.UPLOADED: 'Uploaded',
                ImageStatus.FAILED: 'Upload failed; inspect the draft before retrying',
            }
            lines.append(f"Image: {image_labels[record.image_status]}")
        if record.image_error_message:
            lines.append(f"Image error: {record.image_error_message}")
    return "\n".join([*lines, "", "Nothing was published by this command."])
