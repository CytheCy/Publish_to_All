"""Human-readable reports, without terminal or filesystem side effects."""

from .application import (
    ImageObservationResult, ImageReconciliationResult, Inspection,
    SocialPreviewReadOnlyResult, StoryStatus,
)
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
            lines.append("Reconciliation required before retrying: a remote draft may exist.")
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
