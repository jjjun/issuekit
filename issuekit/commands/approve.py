"""Implementation of the approve command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    parse_issue_id_arg,
)
from issuekit.errors import WorkflowError
from issuekit.gitutil import git_status_short
from issuekit.inputs import active_issue_not_found, require_ascii, resolve_text
from issuekit.store import managed_issue_store
from issuekit.workflow import (
    ensure_assigned_reviewer,
    resolve_reviewer,
    resolve_session,
    validate_assignee,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve a review-stage issue.",
    )
    approve_parser.add_argument("id", help="Issue id to approve.")
    verification_group = approve_parser.add_mutually_exclusive_group(required=True)
    verification_group.add_argument("--verification", help="Verification notes.")
    verification_group.add_argument(
        "--verification-file", help="File containing verification notes."
    )
    summary_group = approve_parser.add_mutually_exclusive_group()
    summary_group.add_argument("--summary", help="Approval summary.")
    summary_group.add_argument("--summary-file", help="File containing the approval summary.")
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


def approve_issue(
    issue_id: int,
    *,
    verification: str,
    summary: str | None = None,
    reviewer: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
    session: str | None = None,
    agent_model: str | None = None,
    agent_reasoning_effort: str | None = None,
) -> Issue:
    require_ascii(
        summary or "",
        verification,
        message="--summary and --verification must be ASCII-only.",
    )

    config = config or IssuekitConfig()
    with managed_issue_store(config, store) as active_store:
        resolved_reviewer = _resolve_api_approval_reviewer(
            active_store, issue_id, reviewer, config
        )
        worker = config.worker_key()
        resolved_session = resolve_session(session)
        return active_store.approve_issue(  # type: ignore[attr-defined]
            issue_id,
            summary=summary if summary is not None else "Approved.",
            verification=verification,
            reviewer=resolved_reviewer,
            worker=worker,
            session=resolved_session,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
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
        validate_assignee(resolved, config)
        issue = store.get_issue(issue_id)  # type: ignore[attr-defined]
        if issue is None:
            raise LookupError(issue_id)
        ensure_assigned_reviewer(issue, reviewer, resolved)
        return resolved

    issue = store.get_issue(issue_id)  # type: ignore[attr-defined]
    if issue is None:
        raise LookupError(issue_id)
    if issue.assignee:
        validate_assignee(issue.assignee, config)
        return issue.assignee
    return resolve_reviewer(None, config, issue=issue)
