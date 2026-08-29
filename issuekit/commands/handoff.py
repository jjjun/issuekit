"""Implementation of review handoff commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import read_text_file, run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, request_changes, submit_for_review


def register(subparsers: argparse._SubParsersAction) -> None:
    submit_review_parser = subparsers.add_parser(
        "submit-review",
        help="Submit an issue for review.",
    )
    submit_review_parser.add_argument("id", help="Issue id to submit.")
    summary_group = submit_review_parser.add_mutually_exclusive_group(required=True)
    summary_group.add_argument("--summary", help="ASCII handoff summary.")
    summary_group.add_argument("--summary-file", help="File containing the ASCII handoff summary.")
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
    notes_group = request_changes_parser.add_mutually_exclusive_group(required=True)
    notes_group.add_argument("--notes", help="ASCII review feedback.")
    notes_group.add_argument("--notes-file", help="File containing ASCII review feedback.")
    request_changes_parser.add_argument("--assignee", help="Implementation assignee to return to.")
    request_changes_parser.add_argument("--reviewer", help="Reviewer assignee returning the issue.")
    request_changes_parser.set_defaults(func=run_request_changes)


def run_submit_review(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        summary = args.summary if args.summary is not None else read_text_file(args.summary_file)
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
        notes = args.notes if args.notes is not None else read_text_file(args.notes_file)
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
