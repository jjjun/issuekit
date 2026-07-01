"""Implementation of the claim command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, claim_issue, claim_next


def run(args) -> int:
    if args.id and args.priority:
        print("--priority can only be used when claiming the next eligible issue.", file=sys.stderr)
        return 1

    def action() -> int:
        issue_id = parse_issue_id_arg(args.id) if args.id else None
        config = load_config(Path.cwd())
        if issue_id is None:
            issue = claim_next(
                args.assignee,
                priority=args.priority,
                config=config,
            )
        else:
            issue = claim_issue(
                issue_id,
                args.assignee,
                config=config,
            )

        if issue is None:
            print(f"status=none assignee={args.assignee}")
            return 0

        print(
            f"id={issue.id} file={issue.relative_path} "
            f"assignee={issue.assignee} stage={issue.stage}"
        )
        return 0

    return run_command(action, errors=(ValueError, TimeoutError, WorkflowError))
