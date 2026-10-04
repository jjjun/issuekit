"""Implementation of the reclaim command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import add_json_flag, print_json, run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.issues.orphans import DEFAULT_STALE_AFTER_SEC
from issuekit.workers.registry import WorkerListingError
from issuekit.workflow import ReclaimResult, reclaim_issue


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
    add_json_flag(reclaim_parser)
    reclaim_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        result = reclaim_issue(
            issue_id,
            force=args.force,
            stale_after_sec=args.stale_after_sec,
            reason=args.reason,
            config=config,
        )
        if args.json:
            print_json(result.to_dict())
            return 0
        _print_result(result)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkflowError, ValueError),
    )


def _print_result(result: ReclaimResult) -> None:
    previous_assignee = result.previous.assignee or "-"
    previous_worker = result.previous.worker or "-"
    print(
        f"Reclaimed issue #{result.issue.id}: "
        f"assignee={previous_assignee} worker={previous_worker} -> pool"
    )
