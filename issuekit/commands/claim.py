"""Implementation of the claim command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import add_guard_override_flags, run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.errors import WorkflowError
from issuekit.workflow import claim_issue, claim_next, require_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    claim_parser = subparsers.add_parser(
        "claim",
        help="Claim an issue for an assignee; defaults to the next eligible issue.",
    )
    claim_parser.add_argument("--id", help="Specific issue id to claim.")
    claim_parser.add_argument("--assignee", help="Assignee to claim for.")
    claim_parser.add_argument("--priority", choices=("high", "medium", "low"), help="Priority filter.")
    add_guard_override_flags(claim_parser)
    claim_parser.set_defaults(func=run)


def run(args) -> int:
    if args.id and args.priority:
        print("--priority can only be used when claiming the next eligible issue.", file=sys.stderr)
        return 1

    def action() -> int:
        issue_id = parse_issue_id_arg(args.id) if args.id else None
        config = load_config(Path.cwd())
        assignee = require_implementer(args.assignee, config, flag="--assignee")
        if issue_id is None:
            issue = claim_next(
                assignee,
                priority=args.priority,
                config=config,
                cwd=Path.cwd(),
                allow_author_guard_override=args.allow_author_session,
                allow_any_branch=args.allow_any_branch,
                no_sync=args.no_sync,
            )
        else:
            issue = claim_issue(
                issue_id,
                assignee,
                config=config,
                cwd=Path.cwd(),
                allow_author_guard_override=args.allow_author_session,
                allow_any_branch=args.allow_any_branch,
                no_sync=args.no_sync,
            )

        if issue is None:
            print(f"status=none assignee={assignee}")
            return 0

        print(
            f"id={issue.id} ref={issue.ref} "
            f"assignee={issue.assignee} stage={issue.stage}"
        )
        if issue.warning:
            print(issue.warning, file=sys.stderr)
        return 0

    return run_command(action, errors=(ValueError, TimeoutError, WorkflowError))
