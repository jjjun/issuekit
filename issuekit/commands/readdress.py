"""Implementation of the readdress command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json
from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import issue_dict
from issuekit.workflow import ReaddressResult, WorkflowError, readdress_issue


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
    readdress_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    readdress_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = int(args.id)
        config = load_config(Path.cwd())
        result = readdress_issue(issue_id, reason=args.reason, config=config)
        if args.json:
            print_json(readdress_result_dict(result))
            return 0
        _print_result(result)
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))


def readdress_result_dict(result: ReaddressResult) -> dict[str, object]:
    issue = issue_dict(result.issue)
    return {
        "id": result.issue.id,
        "ref": result.issue.ref,
        "title": result.issue.title,
        "previous": {
            "target_worker": result.previous.target_worker,
            "stage": result.previous.stage,
            "assignee": result.previous.assignee,
        },
        "expected_target_worker": result.expected_target_worker,
        "actor": result.actor,
        "audit_reason": result.audit_reason,
        "issue": issue,
    }


def _print_result(result: ReaddressResult) -> None:
    print(
        f"Readdressed issue #{result.issue.id}: "
        f"target_worker={result.expected_target_worker} -> repo pool"
    )
