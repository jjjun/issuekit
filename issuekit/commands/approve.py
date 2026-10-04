"""Implementation of the approve command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import add_exclusive_text_file_pair, run_command
from issuekit.config import load_config
from issuekit.core import (
    parse_issue_id_arg,
)
from issuekit.errors import WorkflowError
from issuekit.gitutil import git_status_short
from issuekit.inputs import active_issue_not_found, resolve_text
from issuekit.issues.service import approve_issue


def register(subparsers: argparse._SubParsersAction) -> None:
    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve a review-stage issue.",
    )
    approve_parser.add_argument("id", help="Issue id to approve.")
    add_exclusive_text_file_pair(
        approve_parser,
        "verification",
        help="Verification notes.",
        file_help="File containing verification notes.",
        required=True,
    )
    add_exclusive_text_file_pair(
        approve_parser,
        "summary",
        help="Approval summary.",
        file_help="File containing the approval summary.",
    )
    approve_parser.add_argument("--reviewer", help="Reviewer approving this issue.")
    approve_parser.set_defaults(func=run)


def run(args) -> int:
    issue_id = 0

    def action() -> int:
        nonlocal issue_id
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        if git_status_short(Path.cwd()):
            print(
                "WARNING: approval is being recorded with uncommitted changes in this checkout.",
                file=sys.stderr,
            )
        verification = resolve_text(
            args.verification,
            args.verification_file,
            strip_inline=False,
        ) or ""
        summary = resolve_text(
            args.summary,
            args.summary_file,
            strip_inline=False,
        )
        completed_issue = approve_issue(
            issue_id,
            summary=summary,
            verification=verification,
            reviewer=args.reviewer,
            config=config,
        )

        print(f"Approved issue #{completed_issue.id}: {completed_issue.ref}")
        if summary is not None:
            print(f"summary:\n{summary}")
        print(f"verification:\n{verification}")
        return 0

    return run_command(
        action,
        errors=(OSError, UnicodeError, ValueError, WorkflowError),
        lookup_error=lambda _exc: active_issue_not_found(issue_id),
    )
