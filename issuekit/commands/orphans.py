"""Implementation of the orphans command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.orphans import (
    DEFAULT_STALE_AFTER_SEC,
    EXPIRED_HEARTBEAT,
    StaleClaim,
    list_stale_claims,
    stale_claim_dict,
)
from issuekit.worker_registry import WorkerListingError
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    orphans_parser = subparsers.add_parser(
        "orphans",
        help="List implementing issues whose claiming worker is gone or silent.",
    )
    orphans_parser.add_argument(
        "--stale-after-sec",
        type=float,
        default=DEFAULT_STALE_AFTER_SEC,
        help=(
            "Flag a claim whose worker has not sent a heartbeat for at least "
            f"this many seconds (default: {int(DEFAULT_STALE_AFTER_SEC)})."
        ),
    )
    orphans_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    orphans_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        claims = list_stale_claims(config, stale_after_sec=args.stale_after_sec)
        if args.json:
            print(json.dumps([stale_claim_dict(claim) for claim in claims], indent=2))
            return 0
        if not claims:
            print("No orphaned or stale implementing claims.")
            return 0
        print(f"Orphaned or stale implementing claims: {len(claims)}")
        for claim in claims:
            _print_claim(claim)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkflowError, ValueError),
    )


def _print_claim(claim: StaleClaim) -> None:
    if claim.reason == EXPIRED_HEARTBEAT:
        detail = f"stale: no heartbeat since {claim.last_seen}"
    else:
        detail = "stale: no live registered worker"
    print(
        f"- #{claim.issue.id}: {claim.issue.title} "
        f"[assignee={claim.issue.assignee or '-'} worker={claim.worker}] "
        f"({detail})"
    )
