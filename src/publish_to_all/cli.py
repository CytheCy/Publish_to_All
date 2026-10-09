"""Console entry point; expected errors are displayed without tracebacks."""

import argparse
from pathlib import Path
import sys

from .application import (
    add_substack_body, add_substack_image, add_substack_subtitle, inspect_project, inspect_status,
    inspect_substack_subtitle, reconcile_substack_subtitle,
    inspect_substack_draft, inspect_substack_body_formatting,
    inspect_substack_publish, inspect_substack_social_preview,
    observe_substack_image,
    prepare_substack_draft, repair_substack_title,
    reassociate_substack_version, reconcile_substack, reconcile_substack_image,
    forget_deleted_substack_draft, validate_substack_publish_configuration,
    diagnose_substack_final_action, publish_substack, authorize_substack_retry,
    inspect_substack_history, retire_substack_test_publication, start_new_substack_cycle,
)
from .browser.substack import AuthenticationState, inspect_substack_session
from .browser.medium import run_medium, AuthenticationState as MediumAuthenticationState
from .medium_presentation import format_medium
from .errors import PublishToAllError
from .presentation import (
    format_check, format_preview, format_status, format_substack_body, format_substack_image,
    format_substack_subtitle,
    format_substack_title,
    format_social_preview_inspection,
    format_substack_image_observation,
    format_substack_image_reconciliation,
    format_substack_reassociation, format_substack_session, format_substack_draft,
    format_deleted_draft_cleanup, format_substack_publish_inspection,
    format_final_publish_validation, format_final_action_diagnostic,
    format_substack_publish_execution,
    format_substack_history, format_substack_retirement, format_new_cycle,
)


def _complete_manual_login() -> None:
    print("Complete Substack login manually in the browser, including any email/MFA steps.\n"
          "You may open your account menu to inspect your login. Keep the browser open.\n"
          "Do not enter passwords in this terminal. No draft will be created.", flush=True)
    input("When login is complete, press Enter here to verify and close the browser: ")


def _complete_medium_login() -> None:
    print("Complete Medium's supported sign-in flow manually in the browser.\n"
          "Email links/codes or a third-party provider may be offered.\n"
          "Keep all credentials and codes in the browser; do not enter them here.\n"
          "Keep the browser open. Do not import or create a story.", flush=True)
    input("When finished, press Enter here to check the session and close the browser: ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publish-to-all", description="Inspect local stories and publication state without publishing.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description, formatter in (
        ("check", "Validate the story and configuration without changing files.", format_check),
        ("preview", "Show story metadata and destination readiness.", format_preview),
    ):
        command = commands.add_parser(name, help=description, description=description)
        command.set_defaults(formatter=formatter)
    command = commands.add_parser("status", help="Show local publication state for the current story version.")
    command.set_defaults(formatter=format_status)
    command = commands.add_parser(
        "substack-retire-test-publication",
        help="Verify and retire the known public Substack test artifact locally.",
    )
    command.add_argument("--public-url", required=True, help="Exact known public test-post URL.")
    commands.add_parser(
        "substack-start-new-cycle",
        help="Create a fresh local Substack cycle after test-publication retirement; never contacts Substack.",
    )
    commands.add_parser(
        "substack-history",
        help="Show all local Substack cycles and audit history for the current story version.",
    )
    commands.add_parser("substack", help="Create a Substack draft containing only the title; never publish.")
    commands.add_parser(
        "substack-body",
        help="Insert the story body into the already-linked Substack draft; never publish.",
    )
    command = commands.add_parser(
        'substack-body-spacing-repair',
        help='Remove only the 36 verified cycle-2 separator blanks; never reinsert or publish.',
    )
    command.add_argument('--dry-run', action='store_true', help='Inspect with no remote or SQLite writes.')
    commands.add_parser(
        'substack-body-spacing-reconcile',
        help='Read cycle-2 spacing twice with remote writes blocked; reconcile only local uncertainty.',
    )
    commands.add_parser(
        "substack-subtitle",
        help="Insert Markdown Description as the subtitle of the already-linked draft; never publish.",
    )
    command = commands.add_parser(
        "substack-subtitle-inspect",
        help="Read the linked draft's semantic subtitle control without changing it.",
    )
    command.add_argument("--draft-url", required=True, help="Numeric Substack draft editor URL to inspect.")
    command = commands.add_parser(
        'substack-subtitle-reconcile',
        help='Inspect a failed subtitle without remote writes and reconcile only local subtitle state.',
    )
    command.add_argument('--story-hash', required=True)
    command.add_argument('--cycle-id', required=True, type=int)
    command.add_argument('--draft-url', required=True)
    commands.add_parser(
        "substack-image",
        help="Upload the story's Social Preview image to the populated linked draft; never publish.",
    )
    commands.add_parser(
        "substack-image-observe",
        help="Observe one manual social/post-preview image upload; never select a file or publish.",
    )
    command = commands.add_parser(
        "substack-publish-inspect",
        help="Inspect a prepared linked draft's final publication options; never publish.",
    )
    command.add_argument(
        "--continue-diagnostic-only", action="store_true",
        help="Report safe Continue-control DOM semantics without clicking Continue.",
    )
    commands.add_parser(
        "substack-publish-config-dry-run",
        help="Validate the final publish configuration without changing it or publishing.",
    )
    commands.add_parser(
        "substack-final-action-diagnostic",
        help="Capture raw final-action DOM evidence without clicking it or changing controls.",
    )
    command = commands.add_parser(
        "substack-publish",
        help="Publish the exact prepared Substack draft through the guarded one-click executor.",
    )
    command.add_argument(
        "--dry-run", action="store_true",
        help="Run the authenticated path through final-action revalidation, then stop before clicking.",
    )
    commands.add_parser(
        'substack-publish-reconcile',
        help='Read public evidence for one ambiguous final click; never publish or authorize retries.',
    )
    command = commands.add_parser(
        "substack-image-reconcile",
        help="Reconcile one uncertain Social Preview image upload; never upload or publish.",
    )
    command.add_argument(
        "--story-hash", required=True,
        help="Exact current story hash whose linked draft may be inspected.",
    )
    commands.add_parser(
        "substack-title",
        help="Repair an empty title on the already-linked Substack draft; never publish.",
    )
    command = commands.add_parser(
        "substack-inspect-draft", help="Inspect one supplied Substack draft editor without changing it.",
    )
    command.add_argument("--draft-url", required=True, help="Numeric Substack draft editor URL to inspect.")
    command.add_argument(
        "--body-formatting", action="store_true",
        help="Compare meaningful body blocks read-only, without opening image settings or SQLite.",
    )
    command = commands.add_parser(
        "substack-social-preview-inspect",
        help="Navigate to one draft's Social Preview editor without changing it.",
    )
    command.add_argument("--draft-url", required=True, help="Numeric Substack draft editor URL to inspect.")
    command = commands.add_parser("substack-reconcile", help="Inspect existing drafts without remote changes.")
    reconciliation = command.add_mutually_exclusive_group()
    reconciliation.add_argument(
        "--link", action="store_true",
        help="Link locally after the exact recorded numeric draft editor URL verifies.",
    )
    reconciliation.add_argument("--draft-url", help="Verify and link a user-supplied existing Substack draft editor URL.")
    command.add_argument(
        "--replace-linked-draft", action="store_true",
        help="Explicitly replace the current story's local draft association after verification.",
    )
    commands.add_parser(
        "substack-authorize-retry",
        help="Reverify the exact unpublished draft and explicitly authorize one guarded retry.",
    )
    command = commands.add_parser(
        "substack-reassociate-version",
        help="Locally move one untouched draft association to the current story version.",
    )
    command.add_argument(
        "--from-hash", required=True,
        help="Exact prior story hash that currently owns the untouched draft.",
    )
    command = commands.add_parser(
        "substack-forget-deleted-draft",
        help="Verify a manually deleted draft is gone, then remove its obsolete local record.",
    )
    command.add_argument(
        "--story-hash", required=True,
        help="Exact historical story hash whose obsolete draft record may be removed.",
    )
    commands.add_parser("substack-login", help="Log into Substack manually in a visible browser.")
    command = commands.add_parser("substack-session", help="Check the saved Substack browser session.")
    command.add_argument("--debug", action="store_true", help="Show redacted session diagnostics for any result.")
    for name, description in (
        ("medium-login", "Establish a persistent Medium browser session manually."),
        ("medium-session", "Check Medium authentication read-only."),
        ("medium-import-inspect", "Inspect Medium's Import interface read-only; does not create a draft or submit a source URL."),
    ):
        commands.add_parser(name, help=description, description=description)
    args = parser.parse_args(argv)
    try:
        if args.command in {"medium-login", "medium-session", "medium-import-inspect"}:
            result = run_medium(
                Path("."), complete_login=_complete_medium_login if args.command == "medium-login" else None,
                inspect_import=args.command == "medium-import-inspect",
            )
            print(format_medium(result, inspect_import=args.command == "medium-import-inspect"))
            return 0 if (result.authentication == MediumAuthenticationState.AUTHENTICATED
                         and not result.stopped_reason
                         and (args.command != "medium-import-inspect" or result.interface is not None)) else 1
        if args.command == 'substack-publish-reconcile':
            import json
            from .publication_reconcile import reconcile_publication
            print(json.dumps(reconcile_publication(Path('.')), indent=2))
            return 0
        if args.command == "substack-forget-deleted-draft":
            result = forget_deleted_substack_draft(Path("."), args.story_hash)
            print(format_deleted_draft_cleanup(result))
            return 0
        if args.command == "substack-retire-test-publication":
            result = retire_substack_test_publication(Path("."), args.public_url)
            print(format_substack_retirement(result))
            return 0
        if args.command == "substack-start-new-cycle":
            result = start_new_substack_cycle(Path("."))
            print(format_new_cycle(result))
            return 0
        if args.command == "substack-history":
            print(format_substack_history(inspect_substack_history(Path("."))))
            return 0
        if args.command == "substack-reassociate-version":
            result = reassociate_substack_version(Path("."), args.from_hash)
            print(format_substack_reassociation(result))
            return 0
        if args.command == "substack-reconcile":
            print(reconcile_substack(
                Path("."), link=args.link, draft_url=args.draft_url,
                replace_linked_draft=args.replace_linked_draft,
            ))
            return 0
        if args.command == "substack-authorize-retry":
            result = authorize_substack_retry(Path("."))
            print("Substack retry authorized\n\nDraft: " + (result.substack.draft_url or "None")
                  + "\nPrior final click attempts: "
                  + str(result.substack.final_click_attempt_count)
                  + "\nRetry authorizations: "
                  + str(result.substack.retry_authorization_count)
                  + "\nAuthorized remaining publish attempts: 1"
                  + "\nNo publication action was performed. Nothing was published.")
            return 0
        if args.command == "substack-inspect-draft":
            inspect = inspect_substack_body_formatting if args.body_formatting else inspect_substack_draft
            print(inspect(Path("."), args.draft_url))
            return 0
        if args.command == "substack-social-preview-inspect":
            print(format_social_preview_inspection(
                inspect_substack_social_preview(Path("."), args.draft_url)
            ))
            return 0
        if args.command == "substack":
            print(format_substack_draft(prepare_substack_draft(Path("."))))
            return 0
        if args.command == "substack-body":
            print(format_substack_body(add_substack_body(Path("."))))
            return 0
        if args.command == 'substack-body-spacing-repair':
            from .spacing_repair import repair_body_spacing
            print(repair_body_spacing(Path('.'), dry_run=args.dry_run))
            return 0
        if args.command == 'substack-body-spacing-reconcile':
            from .spacing_reconcile import reconcile_body_spacing
            print(reconcile_body_spacing(Path('.')))
            return 0
        if args.command == "substack-subtitle":
            print(format_substack_subtitle(add_substack_subtitle(Path("."))))
            return 0
        if args.command == 'substack-subtitle-reconcile':
            result = reconcile_substack_subtitle(
                Path('.'), story_hash=args.story_hash, cycle_id=args.cycle_id, draft_url=args.draft_url,
            )
            print(
                f'Remote subtitle classification: {result.evidence.classification.value}\n'
                f'Remote subtitle value: {result.evidence.value!r}\n'
                f'Reason: {result.evidence.reason}\n'
                f'Local subtitle state before: {result.previous.subtitle_status.value}\n'
                f'Local subtitle state after: {result.substack.subtitle_status.value}\n'
                f'Reconciliation required: {result.substack.needs_reconciliation}\n'
                f'Body state: {result.substack.body_status.value}\n'
                f'Image state: {result.substack.image_status.value}\n'
                f'Draft unpublished verified: {result.evidence.unpublished_verified}\n'
                f'Rate limiting: {result.evidence.rate_limited}\n'
                f'SQLite SHA-256 before: {result.database_sha256_before}\n'
                f'SQLite SHA-256 after: {result.database_sha256_after}\n'
                'Remote changes made: No\nSubtitle retried: No\n'
                'Body insertion attempted: No\nNothing published: Yes'
            )
            return 0
        if args.command == "substack-subtitle-inspect":
            diagnostic = inspect_substack_subtitle(Path("."), args.draft_url)
            print("Substack subtitle control diagnostic\n\n"
                  f"Draft: {diagnostic.draft_url}\n"
                  f"Title: {diagnostic.title}\n"
                  f"Subtitle: {diagnostic.subtitle}\n"
                  f"Body: {diagnostic.body_classification}\n"
                  f"Social Preview: {'present' if diagnostic.social_preview_present else 'absent'}\n"
                  f"Editor Saved: {'Yes' if diagnostic.editor_saved else 'No'}\n"
                  f"Published/Sent: {'Yes' if diagnostic.published_or_sent else 'No'}\n"
                  f"Candidate controls: {diagnostic.field_count}\n"
                  f"Visible editable controls: {diagnostic.visible_editable_count}\n"
                  f"Signature: {diagnostic.signature}\n\n"
                  "No remote changes were made.\nNothing was published.")
            return 0
        if args.command == "substack-image":
            print(format_substack_image(add_substack_image(Path("."))))
            return 0
        if args.command == "substack-image-observe":
            print(format_substack_image_observation(observe_substack_image(Path("."))))
            return 0
        if args.command == "substack-publish-inspect":
            print(format_substack_publish_inspection(inspect_substack_publish(
                Path("."), continue_diagnostic_only=args.continue_diagnostic_only,
            )))
            return 0
        if args.command == "substack-publish-config-dry-run":
            result = validate_substack_publish_configuration(Path("."))
            print(format_final_publish_validation(result))
            return 0 if result.validation.ready_for_publish else 1
        if args.command == "substack-final-action-diagnostic":
            print(format_final_action_diagnostic(diagnose_substack_final_action(Path("."))))
            return 0
        if args.command == "substack-publish":
            result = publish_substack(Path("."), dry_run=args.dry_run)
            print(format_substack_publish_execution(result))
            return 0 if result.status.value in {'published', 'dry_run_verified'} else 1
        if args.command == "substack-image-reconcile":
            print(format_substack_image_reconciliation(
                reconcile_substack_image(Path("."), args.story_hash)
            ))
            return 0
        if args.command == "substack-title":
            print(format_substack_title(repair_substack_title(Path("."))))
            return 0
        if args.command in ("substack-login", "substack-session"):
            result = inspect_substack_session(
                Path("."), debug=getattr(args, "debug", False), complete_login=_complete_manual_login if args.command == "substack-login" else None,
            )
            print(format_substack_session(result))
            return 0 if result.authentication == AuthenticationState.AUTHENTICATED else 1
        inspection = inspect_status(Path(".")) if args.command == "status" else inspect_project(Path("."))
        report = args.formatter(inspection)
    except PublishToAllError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        platform = "Medium" if args.command.startswith("medium-") else "Substack"
        print(f"{platform} session check cancelled. No draft was created. Nothing was published.", file=sys.stderr)
        return 1
    print(report)
    return 0
