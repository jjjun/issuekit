"""Implementation of the claim command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, claim_issue, claim_next, resolve_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    claim_parser = subparsers.add_parser(
        "claim",
        help="Claim an issue for an assignee; defaults to the next eligible issue.",
    )
    claim_parser.add_argument("--id", help="Specific issue id to claim.")
    claim_parser.add_argument("--assignee", help="Assignee to claim for.")
    claim_parser.add_argument("--priority", choices=("high", "medium", "low"), help="Priority filter.")
    claim_parser.add_argument(
        "--allow-author-session",
        action="store_true",
        help="Override a local author-session STOP guard for human recovery.",
    )
    claim_parser.add_argument(
        "--allow-any-branch",
        action="store_true",
        help="Override the configured work_branch guard for human recovery.",
    )
    claim_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip the claim-time clean checkout and fast-forward sync guard.",
    )
    claim_parser.set_defaults(func=run)


def run(args) -> int:
    if args.id and args.priority:
        print("--priority can only be used when claiming the next eligible issue.", file=sys.stderr)
        return 1

    def action() -> int:
        issue_id = parse_issue_id_arg(args.id) if args.id else None
        config = load_config(Path.cwd())
        assignee = resolve_implementer(args.assignee, config)
        if assignee is None:
            raise WorkflowError(
                "No implementer is configured. Pass --assignee, set default_implementer, "
                "or configure exactly one enabled assignee."
            )
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
