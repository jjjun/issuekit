"""Implementation of the queue command."""

from __future__ import annotations

from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.workflow import WorkflowError, find_for


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        issues = find_for(
            args.assignee,
            stage=args.stage,
            config=config,
        )

        for issue in issues:
            print(
                f"id={issue.id} file={issue.ref} "
                f"assignee={issue.assignee or '-'} stage={issue.stage or '-'}"
            )
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))
