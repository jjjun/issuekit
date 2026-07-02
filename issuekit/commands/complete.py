"""Implementation of the complete command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import active_issue_not_found, require_ascii, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    parse_issue_id_arg,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    complete_parser = subparsers.add_parser(
        "complete",
        help="Complete an active issue.",
    )
    complete_parser.add_argument("id", help="Issue id to complete.")
    complete_parser.add_argument("--summary", help="Completion summary.")
    complete_parser.add_argument("--verification", help="Verification notes.")
    complete_parser.add_argument(
        "--force",
        action="store_true",
        help="Directly complete an active issue without requiring review stage.",
    )
    complete_parser.set_defaults(func=run)


def run(args) -> int:
    summary = args.summary or ""
    verification = args.verification or ""
    issue_id = 0

    def action() -> int:
        nonlocal issue_id
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        completed_issue = complete_issue(
            issue_id,
            summary=summary,
            verification=verification,
            reviewer=None,
            force=args.force,
            config=config,
        )

        print(f"Completed issue #{completed_issue.id}: {completed_issue.ref}")
        return 0

    return run_command(action, lookup_error=lambda _exc: active_issue_not_found(issue_id))


def complete_issue(
    issue_id: int,
    *,
    summary: str = "",
    verification: str = "",
    reviewer: str | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
    store=None,
) -> Issue:
    require_ascii(
        summary,
        verification,
        message="--summary and --verification must be ASCII-only.",
    )

    config = config or IssuekitConfig()
    owned_store = None
    if store is None:
        from issuekit.store import get_store

        owned_store = get_store(config)
        store = owned_store
    try:
        return store.complete_issue(  # type: ignore[attr-defined]
            issue_id,
            summary=summary,
            verification=verification,
            force=force,
        )
    finally:
        if owned_store is not None:
            owned_store.close()
