"""Implementation of review handoff commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import (
    add_exclusive_text_file_pair,
    add_guard_override_flags,
    run_command,
)
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.inputs import resolve_text
from issuekit.workflow import request_changes, submit_for_review


def register(subparsers: argparse._SubParsersAction) -> None:
    submit_review_parser = subparsers.add_parser(
        "submit-review",
        help="Submit an issue for review.",
    )
    submit_review_parser.add_argument("id", help="Issue id to submit.")
    add_exclusive_text_file_pair(
        submit_review_parser,
        "summary",
        help="ASCII handoff summary.",
        file_help="File containing the ASCII handoff summary.",
        required=True,
    )
    submit_review_parser.add_argument("--branch", help="Branch containing the implementation.")
    submit_review_parser.add_argument("--commit", help="Commit containing the implementation.")
    submit_review_parser.add_argument("--reviewer", help="Reviewer assignee for this handoff.")
    add_guard_override_flags(
        submit_review_parser,
        any_branch=True,
        sync=False,
    )
    submit_review_parser.set_defaults(func=run_submit_review)

    request_changes_parser = subparsers.add_parser(
        "request-changes",
        help="Return an issue to its implementer with requested changes.",
    )
    request_changes_parser.add_argument("id", help="Issue id to return.")
    add_exclusive_text_file_pair(
        request_changes_parser,
        "notes",
        help="ASCII review feedback.",
        file_help="File containing ASCII review feedback.",
        required=True,
    )
    request_changes_parser.add_argument("--assignee", help="Implementation assignee to return to.")
    request_changes_parser.add_argument("--reviewer", help="Reviewer assignee returning the issue.")
    request_changes_parser.set_defaults(func=run_request_changes)


def run_submit_review(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        summary = resolve_text(
            args.summary,
            args.summary_file,
            strip_inline=False,
        ) or ""
        issue = submit_for_review(
            issue_id,
            summary=summary,
            branch=args.branch,
            commit=args.commit,
            reviewer=args.reviewer,
            config=config,
            cwd=Path.cwd(),
            allow_author_guard_override=args.allow_author_session,
            allow_any_branch=args.allow_any_branch,
        )

        print(
            f"id={issue.id} ref={issue.ref} "
            f"assignee={issue.assignee} stage={issue.stage}"
        )
        print(f"summary:\n{summary}")
        return 0

    return run_command(
        action, errors=(OSError, UnicodeError, ValueError, TimeoutError, WorkflowError)
    )


def run_request_changes(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        notes = resolve_text(
            args.notes,
            args.notes_file,
            strip_inline=False,
        ) or ""
        issue = request_changes(
            issue_id,
            notes=notes,
            assignee=args.assignee,
            reviewer=args.reviewer,
            config=config,
        )

        print(
            f"id={issue.id} ref={issue.ref} "
            f"assignee={issue.assignee} stage={issue.stage}"
        )
        print(f"notes:\n{notes}")
        return 0

    return run_command(
        action, errors=(OSError, UnicodeError, ValueError, TimeoutError, WorkflowError)
    )
