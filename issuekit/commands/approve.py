"""Implementation of the approve command."""

from __future__ import annotations

from pathlib import Path

from issuekit.commands._common import active_issue_not_found, require_ascii, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    is_valid_workflow_token,
    parse_issue_id_arg,
)
from issuekit.workflow import (
    WorkflowError,
    ensure_assigned_reviewer,
    resolve_reviewer,
)


def run(args) -> int:
    issue_id = 0

    def action() -> int:
        nonlocal issue_id
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        completed_issue = approve_issue(
            issue_id,
            summary=args.summary,
            verification=args.verification,
            reviewer=args.reviewer,
            force=args.force,
            config=config,
        )

        print(f"Approved issue #{completed_issue.id}: {completed_issue.ref}")
        return 0

    return run_command(action, lookup_error=lambda _exc: active_issue_not_found(issue_id))


def approve_issue(
    issue_id: int,
    *,
    verification: str,
    summary: str | None = None,
    reviewer: str | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
) -> Issue:
    require_ascii(
        summary or "",
        verification,
        message="--summary and --verification must be ASCII-only.",
    )

    config = config or IssuekitConfig()
    from issuekit.store import get_store

    store = get_store(config)
    resolved_reviewer = _resolve_api_approval_reviewer(store, issue_id, reviewer, config)
    worker = config.worker_key()
    return store.approve_issue(  # type: ignore[attr-defined]
        issue_id,
        summary=summary if summary is not None else "Approved.",
        verification=verification,
        reviewer=resolved_reviewer,
        worker=worker,
    )


def _resolve_api_approval_reviewer(
    store: object,
    issue_id: int,
    reviewer: str | None,
    config: IssuekitConfig,
) -> str:
    if reviewer is not None:
        resolved = reviewer.strip()
        if resolved == "auto":
            raise WorkflowError(
                "API approval requires a concrete reviewer; omit --reviewer to use auto resolution."
            )
        _validate_api_approval_reviewer(resolved, config)
        issue = store.get_issue(issue_id)  # type: ignore[attr-defined]
        if issue is None:
            raise LookupError(issue_id)
        ensure_assigned_reviewer(issue, reviewer, resolved)
        return resolved

    issue = store.get_issue(issue_id)  # type: ignore[attr-defined]
    if issue is None:
        raise LookupError(issue_id)
    if issue.assignee:
        _validate_api_approval_reviewer(issue.assignee, config)
        return issue.assignee
    return resolve_reviewer(None, config, issue=issue)


def _validate_api_approval_reviewer(reviewer: str, config: IssuekitConfig) -> None:
    if not is_valid_workflow_token(reviewer):
        raise WorkflowError(f"Invalid assignee token: {reviewer}")
    if reviewer not in config.assignees:
        raise WorkflowError(f"Unknown assignee: {reviewer}")
