"""Request proposal checks from registered target workers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import add_json_flag, print_json, run_command
from issuekit.config import load_config
from issuekit.errors import WorkflowError
from issuekit.proposals import ProposalError
from issuekit.proposals.checks import request_proposal_check


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
    add_json_flag(parser)
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
