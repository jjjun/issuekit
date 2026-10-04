"""Implementation of the dispatch command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.core import issue_dict, parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.issues.service import dispatch_issue


def register(subparsers: argparse._SubParsersAction) -> None:
    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help="Direct an issue to a worker; use readdress to return it to the repo pool.",
    )
    dispatch_parser.add_argument("id", help="Issue id to dispatch.")
    dispatch_parser.add_argument(
        "--target-worker",
        required=True,
        help="Registered worker.repo or worker.repo@machine address.",
    )
    dispatch_parser.add_argument("--assignee", help="Optional implementer assignee.")
    dispatch_parser.add_argument(
        "--stage",
        choices=("todo", "planned"),
        help="Optional destination stage for the directed issue.",
    )
    dispatch_parser.add_argument(
        "--allow-unregistered-worker",
        action="store_true",
        help="Allow directing to a worker that has not registered yet.",
    )
    dispatch_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    dispatch_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        issue = dispatch_issue(
            issue_id,
            target_worker=args.target_worker,
            assignee=args.assignee,
            stage=args.stage,
            allow_unregistered_worker=args.allow_unregistered_worker,
            config=config,
        )
        if args.json:
            output = issue_dict(issue)
            output["target_worker"] = issue.target_worker
            print_json(output)
            return 0
        print(f"Dispatched issue #{issue.id}: target_worker={issue.target_worker}")
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))
