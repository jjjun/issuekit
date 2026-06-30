"""Implementation of the approve command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    has_non_ascii,
    is_valid_workflow_token,
    parse_issue_id_arg,
)
from issuekit.workflow import (
    WorkflowError,
    resolve_reviewer,
)


def run(args) -> int:
    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        completed_issue = approve_issue(
            issues_dir,
            issue_id,
            summary=args.summary,
            verification=args.verification,
            reviewer=args.reviewer,
            force=args.force,
            config=config,
        )
    except (ValueError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except LookupError:
        print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
        return 1
    except UnicodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Approved issue #{completed_issue.id}: {completed_issue.relative_path}")
    return 0


def approve_issue(
    issues_dir: Path | str,
    issue_id: int,
    *,
    verification: str,
    summary: str | None = None,
    reviewer: str | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
) -> Issue:
    if has_non_ascii(summary or "") or has_non_ascii(verification):
        raise ValueError("--summary and --verification must be ASCII-only.")

    config = config or IssuekitConfig()
    from issuekit.store import get_store

    store = get_store(config, issues_dir)
    resolved_reviewer = _resolve_api_approval_reviewer(store, issue_id, reviewer, config)
    return store.approve_issue(  # type: ignore[attr-defined]
        issue_id,
        summary=summary if summary is not None else "Approved.",
        verification=verification,
        reviewer=resolved_reviewer,
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
