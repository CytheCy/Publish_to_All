"""Human-readable reports, without terminal or filesystem side effects."""

from .application import Inspection, StoryStatus
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


def format_check(result: Inspection) -> str:
    story = result.story
    status = " with warnings" if story.warnings else ""
    config_status = "OK" if result.config.publication_url else "Not configured (offline check)"
    lines = [
        f"Story check passed{status}", "",
        f"Story: {story.source.name}", f"Title: {story.metadata.title}",
        f"Image: {story.image.name if story.image else 'none'}",
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
        "Substack draft cover image uploaded", "", "Story:", result.story.metadata.title, "",
        "Draft:", result.substack.draft_url, "", "Image:", result.story.image.name, "",
        "Title:", "Preserved", "", "Body:", "Preserved", "", "Status:",
        "Image uploaded", "", "Nothing was published.",
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
