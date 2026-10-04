"""Implementation of the complete command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import add_exclusive_text_file_pair, run_command
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
    add_exclusive_text_file_pair(
        complete_parser,
        "summary",
        help="Completion summary.",
        file_help="File containing the completion summary.",
    )
    add_exclusive_text_file_pair(
        complete_parser,
        "verification",
        help="Verification notes.",
        file_help="File containing verification notes.",
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
