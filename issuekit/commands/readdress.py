"""Implementation of the readdress command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import add_json_flag, print_json, run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.workflow import ReaddressResult, readdress_issue


def register(subparsers: argparse._SubParsersAction) -> None:
    readdress_parser = subparsers.add_parser(
        "readdress",
        help="Return a directed issue to the repo pool.",
    )
    readdress_parser.add_argument("id", help="Issue id to readdress.")
    readdress_parser.add_argument(
        "--reason",
        help="Optional ASCII audit reason recorded with the readdress event.",
    )
    add_json_flag(readdress_parser)
    readdress_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        result = readdress_issue(issue_id, reason=args.reason, config=config)
        if args.json:
            print_json(result.to_dict())
            return 0
        _print_result(result)
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))


def _print_result(result: ReaddressResult) -> None:
    print(
        f"Readdressed issue #{result.issue.id}: "
        f"target_worker={result.expected_target_worker} -> repo pool"
    )
