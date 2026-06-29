"""Implementation of the approve command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.commands import generate_indexes, validate
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    find_issue_by_id,
    has_non_ascii,
    is_valid_workflow_token,
    parse_issue_id_arg,
    read_active_issues,
)
from issuekit.workflow import (
    WorkflowError,
    ensure_assigned_reviewer,
    ensure_not_self_review,
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

    if not config.api_url:
        generate_indexes.write_index_files(issues_dir, config.recent_count)
    validate_result = validate.run(args)
    if validate_result != 0:
        return validate_result

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
    if config.api_url:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        resolved_reviewer = _resolve_api_approval_reviewer(store, issue_id, reviewer, config)
        return store.approve_issue(  # type: ignore[attr-defined]
            issue_id,
            summary=summary if summary is not None else "Approved.",
            verification=verification,
            reviewer=resolved_reviewer,
        )

    issues_path = Path(issues_dir)
    issue, resolved_reviewer = _resolve_approval_context(
        issues_path,
        issue_id,
        reviewer,
        config,
    )
    if issue.stage != "review" and not force:
        raise WorkflowError(
            f"Issue #{issue_id} must be at the review stage before approval. "
            "Use issuekit complete --force to close a non-review issue."
        )

    approval_summary = summary if summary is not None else f"Approved by {resolved_reviewer}."
    return complete_issue(
        issues_path,
        issue_id,
        summary=approval_summary,
        verification=verification,
        reviewer=resolved_reviewer,
        force=force,
        config=config,
    )


def _resolve_approval_context(
    issues_dir: Path,
    issue_id: int,
    reviewer: str | None,
    config: IssuekitConfig,
) -> tuple[Issue, str]:
    active_issues = read_active_issues(issues_dir)
    issue = find_issue_by_id(active_issues, issue_id)
    if issue is None:
        raise LookupError(issue_id)
    if issue.decode_error:
        raise UnicodeError(f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}")

    resolved_reviewer = resolve_reviewer(reviewer, config, issue=issue)
    if issue.stage == "review":
        ensure_assigned_reviewer(issue, reviewer, resolved_reviewer)
        if not issue.assignee:
            ensure_not_self_review(issue, resolved_reviewer, config)
    return issue, resolved_reviewer


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
