"""Implementation of the claim command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, claim_issue, claim_next


def run(args) -> int:
    if args.id and args.priority:
        print("--priority can only be used when claiming the next eligible issue.", file=sys.stderr)
        return 1
    try:
        issue_id = parse_issue_id_arg(args.id) if args.id else None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        if issue_id is None:
            issue = claim_next(
                issues_dir,
                args.assignee,
                priority=args.priority,
                config=config,
            )
        else:
            issue = claim_issue(
                issues_dir,
                issue_id,
                args.assignee,
                config=config,
            )
    except (TimeoutError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if issue is None:
        print(f"status=none assignee={args.assignee}")
        return 0

    print(
        f"id={issue.id} file={issue.relative_path} "
        f"assignee={issue.assignee} stage={issue.stage}"
    )
    return 0
