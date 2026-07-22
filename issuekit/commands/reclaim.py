"""Implementation of the reclaim command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json
from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import issue_dict
from issuekit.issues.orphans import DEFAULT_STALE_AFTER_SEC
from issuekit.workers.registry import WorkerListingError
from issuekit.workflow import ReclaimResult, WorkflowError, reclaim_issue


def register(subparsers: argparse._SubParsersAction) -> None:
    reclaim_parser = subparsers.add_parser(
        "reclaim",
        help="Return an orphaned implementing claim to the implement pool.",
    )
    reclaim_parser.add_argument("id", help="Issue id to reclaim.")
    reclaim_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip stale-claim detection for human emergency recovery.",
    )
    reclaim_parser.add_argument(
        "--stale-after-sec",
        type=float,
        default=DEFAULT_STALE_AFTER_SEC,
        help=(
            "Require the worker heartbeat to be older than this many seconds "
            f"before reclaiming (default: {int(DEFAULT_STALE_AFTER_SEC)})."
        ),
    )
    reclaim_parser.add_argument(
        "--reason",
        help="Optional ASCII audit reason recorded with the reclaim event.",
    )
    reclaim_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    reclaim_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = int(args.id)
        config = load_config(Path.cwd())
        result = reclaim_issue(
            issue_id,
            force=args.force,
            stale_after_sec=args.stale_after_sec,
            reason=args.reason,
            config=config,
        )
        if args.json:
            print_json(reclaim_result_dict(result))
            return 0
        _print_result(result)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkflowError, ValueError),
    )


def reclaim_result_dict(result: ReclaimResult) -> dict[str, object]:
    issue = issue_dict(result.issue)
    issue["worker"] = result.issue.worker
    return {
        "id": result.issue.id,
        "ref": result.issue.ref,
        "title": result.issue.title,
        "previous": {
            "assignee": result.previous.assignee,
            "worker": result.previous.worker,
            "stage": result.previous.stage,
        },
        "expected_worker": result.expected_worker,
        "actor": result.actor,
        "reason": result.reason,
        "audit_reason": result.audit_reason,
        "issue": issue,
    }


def _print_result(result: ReclaimResult) -> None:
    previous_assignee = result.previous.assignee or "-"
    previous_worker = result.previous.worker or "-"
    print(
        f"Reclaimed issue #{result.issue.id}: "
        f"assignee={previous_assignee} worker={previous_worker} -> pool"
    )
