"""Implementation of the queue command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.core import issue_dict
from issuekit.issues.display import dependency_marker
from issuekit.workflow import WorkflowError, find_for


def register(subparsers: argparse._SubParsersAction) -> None:
    queue_parser = subparsers.add_parser(
        "queue",
        help="List active issues, optionally filtered by assignee.",
    )
    queue_parser.add_argument("--assignee", help="Assignee to list.")
    queue_parser.add_argument("--stage", help="Workflow stage filter.")
    queue_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    queue_parser.add_argument(
        "--with-body",
        action="store_true",
        help="Include issue bodies in JSON output.",
    )
    queue_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        if args.with_body and not args.json:
            raise ValueError("--with-body requires --json.")
        config = load_config(Path.cwd())
        issues = find_for(
            args.assignee or None,
            stage=args.stage,
            config=config,
        )

        if args.json:
            print_json([issue_dict(issue, include_body=args.with_body) for issue in issues])
            return 0

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
