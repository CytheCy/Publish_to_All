"""Console entry point; expected errors are displayed without tracebacks."""

import argparse
from pathlib import Path
import sys

from .application import (
    add_substack_body, add_substack_image, inspect_project, inspect_status,
    inspect_substack_draft, inspect_substack_publish, inspect_substack_social_preview,
    observe_substack_image,
    prepare_substack_draft, repair_substack_title,
    reassociate_substack_version, reconcile_substack, reconcile_substack_image,
    forget_deleted_substack_draft, validate_substack_publish_configuration,
    diagnose_substack_final_action, publish_substack,
)
from .browser.substack import AuthenticationState, inspect_substack_session
from .errors import PublishToAllError
from .presentation import (
    format_check, format_preview, format_status, format_substack_body, format_substack_image,
    format_substack_title,
    format_social_preview_inspection,
    format_substack_image_observation,
    format_substack_image_reconciliation,
    format_substack_reassociation, format_substack_session, format_substack_draft,
    format_deleted_draft_cleanup, format_substack_publish_inspection,
    format_final_publish_validation, format_final_action_diagnostic,
    format_substack_publish_execution,
)


def _complete_manual_login() -> None:
    print("Complete Substack login manually in the browser, including any email/MFA steps.\n"
          "You may open your account menu to inspect your login. Keep the browser open.\n"
          "Do not enter passwords in this terminal. No draft will be created.", flush=True)
    input("When login is complete, press Enter here to verify and close the browser: ")


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
    commands.add_parser("substack", help="Create a Substack draft containing only the title; never publish.")
    commands.add_parser(
        "substack-body",
        help="Insert the story body into the already-linked Substack draft; never publish.",
    )
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
    commands.add_parser(
        "substack-publish",
        help="Publish the exact prepared Substack draft through the guarded one-click executor.",
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
    args = parser.parse_args(argv)
    try:
        if args.command == "substack-forget-deleted-draft":
            result = forget_deleted_substack_draft(Path("."), args.story_hash)
            print(format_deleted_draft_cleanup(result))
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
        if args.command == "substack-inspect-draft":
            print(inspect_substack_draft(Path("."), args.draft_url))
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
            result = publish_substack(Path("."))
            print(format_substack_publish_execution(result))
            return 0 if result.status.value == 'published' else 1
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
        print("Substack session check cancelled. No draft was created. Nothing was published.", file=sys.stderr)
        return 1
    print(report)
    return 0
