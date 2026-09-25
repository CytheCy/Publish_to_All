"""Console entry point; expected errors are displayed without tracebacks."""

import argparse
from pathlib import Path
import sys

from .application import inspect_project, inspect_status, prepare_substack_draft, reconcile_substack
from .browser.substack import AuthenticationState, inspect_substack_session
from .errors import PublishToAllError
from .presentation import format_check, format_preview, format_status, format_substack_session, format_substack_draft


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
    command = commands.add_parser("substack-reconcile", help="Inspect existing drafts without remote changes.")
    command.add_argument("--link", action="store_true", help="Link locally only with exact title, Draft status, and matching recorded attempt URL.")
    commands.add_parser("substack-login", help="Log into Substack manually in a visible browser.")
    command = commands.add_parser("substack-session", help="Check the saved Substack browser session.")
    command.add_argument("--debug", action="store_true", help="Show redacted session diagnostics for any result.")
    args = parser.parse_args(argv)
    try:
        if args.command == "substack-reconcile":
            print(reconcile_substack(Path("."), link=args.link))
            return 0
        if args.command == "substack":
            print(format_substack_draft(prepare_substack_draft(Path("."))))
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
