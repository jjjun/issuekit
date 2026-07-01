"""Implementation of the queue command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.workflow import WorkflowError, find_for


def run(args) -> int:
    config = load_config(Path.cwd())
    try:
        issues = find_for(
            args.assignee,
            stage=args.stage,
            config=config,
        )
    except (WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for issue in issues:
        print(
            f"id={issue.id} file={issue.relative_path} "
            f"assignee={issue.assignee or '-'} stage={issue.stage or '-'}"
        )
    return 0
