"""Implementation of the complete command."""

from __future__ import annotations

from pathlib import Path

from issuekit.commands._common import active_issue_not_found, require_ascii, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    parse_issue_id_arg,
)


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
) -> Issue:
    require_ascii(
        summary,
        verification,
        message="--summary and --verification must be ASCII-only.",
    )

    config = config or IssuekitConfig()
    from issuekit.store import get_store

    store = get_store(config)
    return store.complete_issue(  # type: ignore[attr-defined]
        issue_id,
        summary=summary,
        verification=verification,
        force=force,
    )
