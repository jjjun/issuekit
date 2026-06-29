"""Implementation of the complete command."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

from issuekit.commands import generate_indexes, validate
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    find_issue_by_id,
    parse_issue_id_arg,
    read_active_issues,
    format_issue_frontmatter,
    has_non_ascii,
    parse_issue_frontmatter,
    passthrough_frontmatter,
    read_issues,
    write_issue_atomic,
)
from issuekit.workflow import WorkflowError, ensure_not_self_review, resolve_reviewer


def run(args) -> int:
    summary = args.summary or ""
    verification = args.verification or ""

    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        completed_issue = complete_issue(
            issues_dir,
            issue_id,
            summary=summary,
            verification=verification,
            reviewer=None,
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

    print(f"Completed issue #{completed_issue.id}: {completed_issue.relative_path}")
    return 0


def complete_issue(
    issues_dir: Path | str,
    issue_id: int,
    *,
    summary: str = "",
    verification: str = "",
    reviewer: str | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
) -> Issue:
    if has_non_ascii(summary) or has_non_ascii(verification):
        raise ValueError("--summary and --verification must be ASCII-only.")

    config = config or IssuekitConfig()
    if config.api_url:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        return store.complete_issue(  # type: ignore[attr-defined]
            issue_id,
            summary=summary,
            verification=verification,
            force=force,
        )

    issues_path = Path(issues_dir)
    active_issues = read_active_issues(issues_path)
    issue = find_issue_by_id(active_issues, issue_id)
    if issue is None:
        raise LookupError(issue_id)
    if issue.decode_error:
        raise UnicodeError(f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}")
    reviewer = resolve_reviewer(reviewer, config, issue=issue)
    ensure_not_self_review(issue, reviewer, config)

    if config.require_review_before_complete and not force and issue.stage != "review":
        raise WorkflowError(
            f"Issue #{issue_id} must reach the review stage before completion. "
            "Use submit_for_review / approve, or pass --force to bypass."
        )

    completed_date = date.today().isoformat()
    frontmatter = parse_issue_frontmatter(issue.content)
    data = {
        **passthrough_frontmatter(frontmatter.data),
        "id": issue.id,
        "status": "completed",
        "priority": issue.priority or "medium",
        "created": issue.created or completed_date,
        "completed": completed_date,
        "assignee": "",
        "stage": "done",
        "implementer": "",
        "author": issue.author,
        "title": issue.title,
    }
    next_content = format_issue_frontmatter(data) + _append_completion_note(
        frontmatter.body.strip("\n"),
        summary=summary,
        verification=verification,
        completed_date=completed_date,
    )
    completed_dir = issues_path / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    completed_path = completed_dir / issue.file_name

    write_issue_atomic(issue.file_path, next_content)
    issue.file_path.replace(completed_path)
    completed_issue = next(
        candidate for candidate in read_issues(issues_path, "completed") if candidate.id == issue_id
    )
    return completed_issue


def _append_completion_note(
    body: str,
    *,
    summary: str,
    verification: str,
    completed_date: str,
) -> str:
    lines = [
        "",
        f"**Completed**: {completed_date}",
        "",
        "## Completion Notes",
        "",
    ]
    lines.append(f"- {summary}" if summary else "- Completed the tracked scope.")
    if verification:
        lines.append(f"- Verification: `{verification}`")
    note = "\n".join(lines)
    return f"{body.rstrip()}\n{note}\n"
