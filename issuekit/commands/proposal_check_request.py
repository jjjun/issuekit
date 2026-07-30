"""Request proposal checks from registered target workers."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.proposals import ProposalError
from issuekit.proposals.api import api_client
from issuekit.workers.addressing import resolve_registered_worker_address
from issuekit.workflow import WorkflowError


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
        check = client.create_proposal_check(
            proposal_id,
            target_worker=target_worker,
            project=to,
        )
    result = dict(check)
    result["worker_auto_selected"] = worker is None
    return result
