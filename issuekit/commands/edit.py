"""Implementation of the edit command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import (
    print_json,
    run_command,
)
from issuekit.config import load_config
from issuekit.core import issue_dict, parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.issues.service import edit_issue


def register(subparsers: argparse._SubParsersAction) -> None:
    edit_parser = subparsers.add_parser(
        "edit",
        help="Edit an API-backed issue title, body, or priority.",
    )
    edit_parser.add_argument("id", help="Issue id to edit.")
    edit_parser.add_argument("--title", help="Replacement issue title.")
    edit_body_group = edit_parser.add_mutually_exclusive_group()
    edit_body_group.add_argument("--body", help="Replacement inline issue body.")
    edit_body_group.add_argument("--body-file", help="File containing replacement issue body.")
    edit_body_group.add_argument("--append", help="Inline text to append to the issue body.")
    edit_body_group.add_argument("--append-file", help="File containing text to append to the issue body.")
    edit_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        help="Replacement issue priority.",
    )
    edit_parser.add_argument(
        "--depends-on",
        action="append",
        dest="depends_on",
        help="Replace upstream dependency refs with one or more project#proposal:123 values.",
    )
    edit_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow editing an issue that is already in flight.",
    )
    edit_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    edit_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        issue = edit_issue(
            parse_issue_id_arg(args.id),
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            append=args.append,
            append_file=args.append_file,
            priority=args.priority,
            depends_on=args.depends_on,
            force=args.force,
            config=config,
        )
        if args.json:
            print_json(issue_dict(issue, include_body=True))
        else:
            print(f"Updated issue: {issue.ref}")
        return 0

    return run_command(
        action,
        errors=(OSError, UnicodeError, ValueError, WorkflowError),
    )
