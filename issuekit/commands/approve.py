"""Implementation of the approve command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.commands import generate_indexes, validate
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, find_issue_by_id, parse_issue_id_arg, read_active_issues
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
    config = config or IssuekitConfig()
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
