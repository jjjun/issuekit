"""Implementation of the claim command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import load_config
from issuekit.workflow import WorkflowError, claim_next


def run(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        issue = claim_next(
            issues_dir,
            args.assignee,
            priority=args.priority,
            config=config,
        )
    except (TimeoutError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if issue is None:
        print(f"status=none assignee={args.assignee}")
        return 0

    write_index_files(issues_dir, config.recent_count)
    print(
        f"id={issue.id} file={issue.relative_path} "
        f"assignee={issue.assignee} stage={issue.stage}"
    )
    return 0
