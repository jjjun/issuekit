"""Implementation of the complete command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import (
    parse_issue_id_arg,
)
from issuekit.errors import WorkflowError
from issuekit.inputs import active_issue_not_found, resolve_text
from issuekit.issues.service import complete_issue


def register(subparsers: argparse._SubParsersAction) -> None:
    complete_parser = subparsers.add_parser(
        "complete",
        help="Complete an active issue.",
    )
    complete_parser.add_argument("id", help="Issue id to complete.")
    summary_group = complete_parser.add_mutually_exclusive_group()
    summary_group.add_argument("--summary", help="Completion summary.")
    summary_group.add_argument("--summary-file", help="File containing the completion summary.")
    verification_group = complete_parser.add_mutually_exclusive_group()
    verification_group.add_argument("--verification", help="Verification notes.")
    verification_group.add_argument(
        "--verification-file", help="File containing verification notes."
    )
    complete_parser.add_argument(
        "--force",
        action="store_true",
        help="Directly complete an active issue without requiring review stage.",
    )
    complete_parser.set_defaults(func=run)


def run(args) -> int:
    issue_id = 0

    def action() -> int:
        nonlocal issue_id
        issue_id = parse_issue_id_arg(args.id)
        summary = resolve_text(
            args.summary,
            args.summary_file,
            strip_inline=False,
            prefer="file",
        ) or ""
        verification = resolve_text(
            args.verification,
            args.verification_file,
            strip_inline=False,
            prefer="file",
        ) or ""
        config = load_config(Path.cwd())
        completed_issue = complete_issue(
            issue_id,
            summary=summary,
            verification=verification,
            force=args.force,
            config=config,
        )

        print(f"Completed issue #{completed_issue.id}: {completed_issue.ref}")
        if summary:
            print(f"summary:\n{summary}")
        if verification:
            print(f"verification:\n{verification}")
        return 0

    return run_command(
        action,
        errors=(OSError, UnicodeError, ValueError, WorkflowError),
        lookup_error=lambda _exc: active_issue_not_found(issue_id),
    )
