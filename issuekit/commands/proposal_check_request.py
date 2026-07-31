"""Request proposal checks from registered target workers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.proposals import ProposalError
from issuekit.proposals.api import api_client
from issuekit.timestamps import parse_timestamp
from issuekit.workers.addressing import (
    registered_worker_row,
    resolve_registered_worker_address,
)
from issuekit.workflow import WorkflowError

PROPOSAL_CHECK_WORKER_STALE_AFTER_SEC = 300.0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "proposal-check-request",
        help="Request evaluation of a pending proposal by a registered target worker.",
    )
    parser.add_argument("--to", required=True, help="Target project name.")
    parser.add_argument("--proposal", required=True, type=int, help="Proposal id to check.")
    parser.add_argument(
        "--worker",
        help="Registered worker.repo or worker.repo@machine address.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        result = request_proposal_check(
            load_config(Path.cwd()),
            to=args.to,
            proposal_id=int(args.proposal),
            worker=args.worker,
        )
        if args.json:
            print_json(result)
            return 0
        for warning in result.get("warnings", []):
            print(f"WARNING: {warning}", file=sys.stderr)
        if result["worker_auto_selected"]:
            print(f"Automatically selected worker: {result['target_worker']}")
        action_name = "Created" if result["was_created"] else "Existing pending"
        print(
            f"{action_name} proposal check #{result['id']}: "
            f"target_worker={result['target_worker']}"
        )
        return 0

    return run_command(action, errors=(ProposalError, ValueError, WorkflowError))


def request_proposal_check(
    config: IssuekitConfig,
    *,
    to: str,
    proposal_id: int,
    worker: str | None = None,
) -> dict:
    with api_client(config, project=to) as client:
        workers = client.list_workers(project=to)
        target_worker = resolve_registered_worker_address(
            workers,
            project=to,
            address=worker,
        )
        selected_worker = registered_worker_row(workers, target_worker)
        check = client.create_proposal_check(
            proposal_id,
            target_worker=target_worker,
            project=to,
        )
    result = dict(check)
    result["worker_auto_selected"] = worker is None
    warning = _worker_liveness_warning(target_worker, selected_worker)
    if warning is not None:
        result["warnings"] = [warning]
    return result


def _worker_liveness_warning(
    target_worker: str,
    worker: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> str | None:
    if worker is None:
        return None
    status = str(worker.get("status") or "unknown")
    last_seen = str(worker.get("last_seen") or "unknown")
    seen = parse_timestamp(worker.get("last_seen"))
    age_seconds = (
        None
        if seen is None
        else max(0, int(((now or datetime.now(UTC)) - seen).total_seconds()))
    )
    if status != "offline" and (
        age_seconds is None or age_seconds <= PROPOSAL_CHECK_WORKER_STALE_AFTER_SEC
    ):
        return None
    age = "unknown" if age_seconds is None else f"{age_seconds}s"
    return (
        f"Target worker {target_worker} may be unreachable: "
        f"status={status}, last_seen={last_seen}, age={age}."
    )
