"""Implementation of review handoff commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, request_changes, submit_for_review


def register(subparsers: argparse._SubParsersAction) -> None:
    submit_review_parser = subparsers.add_parser(
        "submit-review",
        help="Submit an issue for review.",
    )
    submit_review_parser.add_argument("id", help="Issue id to submit.")
    submit_review_parser.add_argument("--summary", required=True, help="ASCII handoff summary.")
    submit_review_parser.add_argument("--branch", help="Branch containing the implementation.")
    submit_review_parser.add_argument("--commit", help="Commit containing the implementation.")
    submit_review_parser.add_argument("--reviewer", help="Reviewer assignee for this handoff.")
    submit_review_parser.add_argument(
        "--allow-author-session",
        action="store_true",
        help="Override a local author-session STOP guard for human recovery.",
    )
    submit_review_parser.add_argument(
        "--allow-any-branch",
        action="store_true",
        help="Override the configured work_branch guard for human recovery.",
    )
    submit_review_parser.set_defaults(func=run_submit_review)

    request_changes_parser = subparsers.add_parser(
        "request-changes",
        help="Return an issue to its implementer with requested changes.",
    )
    request_changes_parser.add_argument("id", help="Issue id to return.")
    request_changes_parser.add_argument("--notes", required=True, help="ASCII review feedback.")
    request_changes_parser.add_argument("--assignee", help="Implementation assignee to return to.")
    request_changes_parser.add_argument("--reviewer", help="Reviewer assignee returning the issue.")
    request_changes_parser.set_defaults(func=run_request_changes)


def run_submit_review(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        issue = submit_for_review(
            issue_id,
            summary=args.summary,
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
        return 0

    return run_command(action, errors=(ValueError, TimeoutError, WorkflowError))


def run_request_changes(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        issue = request_changes(
            issue_id,
            notes=args.notes,
            assignee=args.assignee,
            reviewer=args.reviewer,
            config=config,
        )

        print(
            f"id={issue.id} ref={issue.ref} "
            f"assignee={issue.assignee} stage={issue.stage}"
        )
        return 0

    return run_command(action, errors=(ValueError, TimeoutError, WorkflowError))
