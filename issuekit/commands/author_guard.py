"""Commands for inspecting and clearing the local author-session guard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.errors import WorkflowError
from issuekit.guards.author import clear_author_guard, guards_dict, read_author_guards, stop_message
from issuekit.guards.separation import AUTHOR_GUARD_HELP


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "author-guard",
        help="Inspect or clear the local author-session STOP guard.",
        description="Inspect, check, or clear the local author-session STOP guard.",
        epilog=AUTHOR_GUARD_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    actions = parser.add_subparsers(dest="author_guard_action", metavar="<action>")

    show_parser = actions.add_parser("show", help="Show the current local author guards.")
    show_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print JSON output.",
    )
    show_parser.set_defaults(func=run_show)

    check_parser = actions.add_parser(
        "check",
        help="Fail when a local issue guard is present.",
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print JSON output.",
    )
    check_parser.set_defaults(func=run_check)

    clear_parser = actions.add_parser(
        "clear",
        help="Clear local author guards after handoff or human recovery.",
    )
    clear_parser.add_argument("--ref", help="Clear only the guard with this issue or proposal ref.")
    clear_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print JSON output.",
    )
    clear_parser.set_defaults(func=run_clear)

    parser.set_defaults(func=run_show)


def run_show(args) -> int:
    def action() -> int:
        guards = read_author_guards(Path.cwd())
        if getattr(args, "json", False):
            print_json(
                {
                    "authorGuards": guards_dict(guards),
                }
            )
            return 0
        if not guards:
            print("No author-session guard.")
            return 0
        for guard in guards:
            print(stop_message(guard))
        print("Next: run `issuekit author-guard clear` after handoff to clear all guards.")
        return 0

    return run_command(action, errors=(OSError, ValueError, WorkflowError))


def run_check(args) -> int:
    def action() -> int:
        guards = read_author_guards(Path.cwd())
        issue_guards = tuple(guard for guard in guards if guard.kind == "issue")
        blocking = bool(issue_guards)
        if args.json:
            print_json(
                {
                    "ok": not blocking,
                    "blocking": blocking,
                    "authorGuards": guards_dict(guards),
                }
            )
        if not blocking:
            if not args.json:
                print("Author guard check passed: no local issue guard blocks lifecycle commands.")
                for guard in guards:
                    print(f"Proposal guard information: {stop_message(guard)}")
            return 0
        if not args.json:
            for guard in issue_guards:
                print(stop_message(guard), file=sys.stderr)
        return 1

    return run_command(action, errors=(OSError, ValueError, WorkflowError))


def run_clear(args) -> int:
    def action() -> int:
        cleared = clear_author_guard(Path.cwd(), ref=args.ref)
        if args.json:
            print_json({"cleared": cleared, "ref": args.ref})
            return 0
        if cleared:
            suffix = f" for {args.ref}" if args.ref else "s"
            print(f"Cleared author-session guard{suffix}.")
        else:
            suffix = f" for {args.ref}" if args.ref else ""
            print(f"No author-session guard found{suffix}.")
        return 0

    return run_command(action, errors=(OSError, ValueError, WorkflowError))
