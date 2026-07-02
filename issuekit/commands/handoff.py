"""Implementation of review handoff commands."""

from __future__ import annotations

from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, request_changes, submit_for_review


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
