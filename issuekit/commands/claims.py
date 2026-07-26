"""Implementation of the claims command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.workers.registry import (
    WorkerClaim,
    WorkerListingError,
    list_worker_claims,
    worker_claim_dict,
)
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    claims_parser = subparsers.add_parser(
        "claims",
        help="List active worker claims for this project.",
    )
    claims_parser.add_argument("--worker", help="Filter claims by worker key.")
    claims_parser.add_argument("--stage", help="Filter claims by workflow stage.")
    claims_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    claims_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        claims = list_worker_claims(
            config,
            worker=args.worker,
            stage=args.stage,
        )
        if args.json:
            print_json([worker_claim_dict(claim) for claim in claims])
            return 0
        if not claims:
            print("No active worker claims.")
            return 0
        print(f"Active worker claims: {len(claims)}")
        for claim in claims:
            _print_claim(claim)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkflowError, ValueError),
    )


def _print_claim(claim: WorkerClaim) -> None:
    issue = claim.issue
    parts = [
        f"assignee={issue.assignee or '-'}",
        f"stage={issue.stage or '-'}",
        f"worker={claim.worker}",
    ]
    if issue.target_worker:
        parts.append(f"target_worker={issue.target_worker}")
    if claim.claimed:
        parts.append(f"claimed={claim.claimed}")
    if claim.last_transition:
        parts.append(f"last_transition={claim.last_transition}")
    print(f"- #{issue.id}: {issue.title} [{' '.join(parts)}] ({issue.ref})")
