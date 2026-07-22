"""Implementation of the queue command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.issues.display import dependency_marker
from issuekit.workflow import WorkflowError, find_for


def register(subparsers: argparse._SubParsersAction) -> None:
    queue_parser = subparsers.add_parser(
        "queue",
        help="List active issues for an assignee.",
    )
    queue_parser.add_argument("--assignee", required=True, help="Assignee to list.")
    queue_parser.add_argument("--stage", help="Workflow stage filter.")
    queue_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        issues = find_for(
            args.assignee,
            stage=args.stage,
            config=config,
        )

        for issue in issues:
            parts = [
                f"id={issue.id}",
                f"ref={issue.ref}",
                f"assignee={issue.assignee or '-'}",
                f"stage={issue.stage or '-'}",
            ]
            if issue.target_worker:
                parts.append(f"target_worker={issue.target_worker}")
            marker = dependency_marker(issue)
            if marker:
                parts.append(marker)
            print(" ".join(parts))
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))
