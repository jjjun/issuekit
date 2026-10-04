"""Implementation of the issue planning command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.core import Issue, issue_dict, parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.store import managed_issue_store


def register(subparsers: argparse._SubParsersAction) -> None:
    plan_parser = subparsers.add_parser(
        "plan",
        help="Hold or release an issue in the implementation pool.",
    )
    plan_parser.add_argument("id", help="Issue id to plan.")
    plan_parser.add_argument(
        "--stage",
        choices=("planned", "todo"),
        default="planned",
        help="Destination planning stage (default: planned).",
    )
    plan_parser.add_argument("--note", help="Optional note recorded with the planning transition.")
    plan_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    plan_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        with managed_issue_store(config) as store:
            issue = store.plan_issue(issue_id, stage=args.stage, note=args.note)
        if args.json:
            print_json(issue_dict(issue))
            return 0
        _print_issue(issue)
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))


def _print_issue(issue: Issue) -> None:
    print(f"Planned issue #{issue.id}: stage={issue.stage}")
