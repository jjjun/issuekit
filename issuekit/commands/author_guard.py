"""Commands for inspecting and clearing the local author-session guard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from issuekit.author_guard import clear_author_guard, guard_dict, read_author_guard, stop_message
from issuekit.commands._common import run_command
from issuekit.separation_duties import AUTHOR_GUARD_HELP
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "author-guard",
        help="Inspect or clear the local author-session STOP guard.",
        description="Inspect, check, or clear the local author-session STOP guard.",
        epilog=AUTHOR_GUARD_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="author_guard_action", metavar="<action>")

    show_parser = actions.add_parser("show", help="Show the current local author guard.")
    show_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    show_parser.set_defaults(func=run_show)

    check_parser = actions.add_parser(
        "check",
        help="Fail when a local author guard is present.",
    )
    check_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    check_parser.set_defaults(func=run_check)

    clear_parser = actions.add_parser(
        "clear",
        help="Clear the local author guard after handoff or human recovery.",
    )
    clear_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    clear_parser.set_defaults(func=run_clear)

    parser.set_defaults(func=run_show)


def run_show(args) -> int:
    def action() -> int:
        guard = read_author_guard(Path.cwd())
        if getattr(args, "json", False):
            print(json.dumps({"authorGuard": guard_dict(guard)}, indent=2))
            return 0
        if guard is None:
            print("No author-session guard.")
            return 0
        print(stop_message(guard))
        print("Next: stop this session, or run `issuekit author-guard clear` after handoff.")
        return 0

    return run_command(action, errors=(OSError, ValueError, WorkflowError))


def run_check(args) -> int:
    def action() -> int:
        guard = read_author_guard(Path.cwd())
        if args.json:
            print(json.dumps({"ok": guard is None, "authorGuard": guard_dict(guard)}, indent=2))
        if guard is None:
            if not args.json:
                print("Author guard check passed: no local author-session guard.")
            return 0
        if not args.json:
            print(stop_message(guard), file=sys.stderr)
        return 1

    return run_command(action, errors=(OSError, ValueError, WorkflowError))


def run_clear(args) -> int:
    def action() -> int:
        cleared = clear_author_guard(Path.cwd())
        if args.json:
            print(json.dumps({"cleared": cleared}, indent=2))
            return 0
        print("Cleared author-session guard." if cleared else "No author-session guard to clear.")
        return 0

    return run_command(action, errors=(OSError, ValueError, WorkflowError))
