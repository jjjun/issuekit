"""Agent handoff workflow transitions."""

from __future__ import annotations

from pathlib import Path

from issuekit.config import IssuekitConfig
from issuekit.core import (
    Issue,
    Frontmatter,
    VALID_ISSUE_PRIORITIES,
    find_issue_by_id,
    read_active_issues,
    passthrough_frontmatter,
    format_issue_frontmatter,
    has_non_ascii,
    is_valid_workflow_token,
    parse_issue_frontmatter,
    write_issue_atomic,
)


READY_STAGES = {"", "todo", "changes_requested"}
CLAIMABLE_STATUSES = {"active", "in_progress"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
AUTO_REVIEWER = "auto"


class WorkflowError(RuntimeError):
    """Raised when a workflow transition cannot be completed."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def claim_next(
    issues_dir: Path | str,
    assignee: str,
    *,
    priority: str | None = None,
    config: IssuekitConfig | None = None,
    timeout: float = 10.0,
) -> Issue | None:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("implementing", config)
    if priority is not None and priority not in VALID_ISSUE_PRIORITIES:
        raise WorkflowError(f"Invalid priority: {priority}")
    if not config.use_filesystem_store:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        return store.claim_next(assignee=assignee, priority=priority)  # type: ignore[attr-defined]

    issues_path = Path(issues_dir)
    issues = read_active_issues(issues_path)
    candidates = [
        issue
        for issue in issues
        if not issue.decode_error
        and issue.issue_status in CLAIMABLE_STATUSES
        and issue.stage in READY_STAGES
        and issue.assignee in {"", assignee}
        and (priority is None or issue.priority == priority)
    ]
    if not candidates:
        return None
    issue = sorted(candidates, key=lambda item: (PRIORITY_RANK.get(item.priority, 99), item.id or 0))[0]
    ensure_not_author_self_claim(issue, assignee)
    return _write_active_issue(
        issues_path,
        issue,
        status="in_progress",
        assignee=assignee,
        stage="implementing",
        implementer=assignee,
    )


def claim_issue(
    issues_dir: Path | str,
    issue_id: int,
    assignee: str,
    *,
    config: IssuekitConfig | None = None,
    timeout: float = 10.0,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("implementing", config)
    if not config.use_filesystem_store:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        return store.claim_issue(issue_id, assignee=assignee)  # type: ignore[attr-defined]

    issues_path = Path(issues_dir)
    issue = _find_active_issue(issues_path, issue_id, active_issues=read_active_issues(issues_path))
    if issue.issue_status not in CLAIMABLE_STATUSES:
        raise WorkflowError(
            f"Issue #{issue_id} has status {issue.issue_status or issue.status}; "
            "only active or in_progress issues can be implemented."
        )
    if issue.stage not in READY_STAGES | {"implementing"}:
        raise WorkflowError(
            f"Issue #{issue_id} is at stage {issue.stage or 'todo'}, "
            "not ready for implementation."
        )
    if issue.assignee not in {"", assignee}:
        raise WorkflowError(f"Issue #{issue_id} is assigned to {issue.assignee}, not {assignee}.")
    ensure_not_author_self_claim(issue, assignee)
    return _write_active_issue(
        issues_path,
        issue,
        status="in_progress",
        assignee=assignee,
        stage="implementing",
        implementer=assignee,
    )


def submit_for_review(
    issues_dir: Path | str,
    issue_id: int,
    *,
    summary: str,
    branch: str | None = None,
    commit: str | None = None,
    assignee: str = "codex",
    reviewer: str | None = None,
    config: IssuekitConfig | None = None,
    timeout: float = 10.0,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("review", config)
    _validate_ascii_text(summary, "--summary")
    _validate_ascii_text(branch or "", "--branch")
    _validate_ascii_text(commit or "", "--commit")
    if not config.use_filesystem_store:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        return store.submit_for_review(  # type: ignore[attr-defined]
            issue_id,
            summary=summary,
            branch=branch,
            commit=commit,
            reviewer=reviewer,
        )

    issues_path = Path(issues_dir)
    issue = _find_active_issue(issues_path, issue_id, active_issues=read_active_issues(issues_path))
    if issue.assignee != assignee:
        raise WorkflowError(
            f"Issue #{issue_id} is assigned to {issue.assignee or 'no one'}, not {assignee}."
        )
    if reviewer is None and config.default_reviewer == AUTO_REVIEWER:
        resolved_reviewer = ""
    else:
        resolved_reviewer = resolve_reviewer(reviewer, config, issue=issue)
        if resolved_reviewer and issue.implementer and resolved_reviewer == issue.implementer:
            raise WorkflowError(
                f"Issue #{issue_id} was implemented by {issue.implementer}; "
                "omit `reviewer` if default_reviewer is auto to use the open "
                "review pool, otherwise name a different reviewer."
            )
        ensure_not_self_review(issue, resolved_reviewer, config)
    note = _handoff_note(summary=summary, branch=branch or "", commit=commit or "")
    return _write_active_issue(
        issues_path,
        issue,
        assignee=resolved_reviewer,
        stage="review",
        extra_body=note,
    )


def request_changes(
    issues_dir: Path | str,
    issue_id: int,
    *,
    notes: str,
    reviewer: str | None = None,
    assignee: str | None = None,
    config: IssuekitConfig | None = None,
    timeout: float = 10.0,
) -> Issue:
    config = config or IssuekitConfig()
    if assignee is not None:
        _validate_assignee(assignee, config)
    _validate_stage("changes_requested", config)
    _validate_ascii_text(notes, "--notes")
    if not config.use_filesystem_store:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        return store.request_changes(  # type: ignore[attr-defined]
            issue_id,
            notes=notes,
            reviewer=reviewer,
            assignee=assignee,
        )

    issues_path = Path(issues_dir)
    issue = _find_active_issue(issues_path, issue_id, active_issues=read_active_issues(issues_path))
    resolved_reviewer = resolve_reviewer(reviewer, config, issue=issue)
    ensure_assigned_reviewer(issue, reviewer, resolved_reviewer)
    if not issue.assignee:
        ensure_not_self_review(issue, resolved_reviewer, config)
    assignee = assignee or issue.implementer or "codex"
    _validate_assignee(assignee, config)
    return _write_active_issue(
        issues_path,
        issue,
        assignee=assignee,
        stage="changes_requested",
        extra_body=_review_feedback_note(notes),
    )


def find_for(
    issues_dir: Path | str,
    assignee: str | None = None,
    *,
    stage: str | None = None,
    config: IssuekitConfig | None = None,
) -> list[Issue]:
    config = config or IssuekitConfig()
    if assignee:
        _validate_assignee(assignee, config)
    if stage:
        _validate_stage(stage, config)

    from issuekit.store import get_store

    return get_store(config, issues_dir).find_for(assignee, stage)


def _write_active_issue(
    issues_dir: Path,
    issue: Issue,
    *,
    status: str | None = None,
    assignee: str | None = None,
    stage: str | None = None,
    implementer: str | None = None,
    extra_body: str = "",
) -> Issue:
    frontmatter = parse_issue_frontmatter(issue.content)
    data = {
        **passthrough_frontmatter(frontmatter.data),
        "id": issue.id,
        "status": status or issue.issue_status or issue.status,
        "priority": issue.priority or "medium",
        "created": issue.created,
        "completed": issue.completed,
        "assignee": issue.assignee if assignee is None else assignee,
        "stage": issue.stage if stage is None else stage,
        "implementer": issue.implementer if implementer is None else implementer,
        "author": issue.author,
        "title": issue.title,
    }
    body = frontmatter.body.strip("\n")
    if extra_body:
        body = f"{body}\n{extra_body.rstrip()}"
    content = f"{format_issue_frontmatter(data)}{body}\n"
    write_issue_atomic(issue.file_path, content)
    return _build_updated_issue(
        issue,
        frontmatter,
        data,
        issue_content=content,
        body=body,
    )


def _find_active_issue(
    issues_dir: Path,
    issue_id: int,
    *,
    active_issues: list[Issue] | None = None,
) -> Issue:
    issue = find_issue_by_id(active_issues if active_issues is not None else read_active_issues(issues_dir), issue_id)
    if issue is None:
        raise WorkflowError(f"Active issue #{issue_id} was not found.")
    if issue.decode_error:
        raise WorkflowError(f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}")
    return issue


def _build_updated_issue(
    issue: Issue,
    frontmatter: Frontmatter,
    data: dict[str, object],
    *,
    issue_content: str,
    body: str,
) -> Issue:
    return Issue(
        id=issue.id,
        file_name_id=issue.file_name_id,
        file_name=issue.file_name,
        file_path=issue.file_path,
        relative_path=issue.relative_path,
        title=str(data["title"]),
        status=issue.status,
        issue_status=str(data["status"]),
        created=issue.created or "",
        completed=str(data["completed"]),
        priority=str(data["priority"]),
        assignee=str(data["assignee"]),
        stage=str(data["stage"]),
        implementer=str(data["implementer"]),
        author=str(data["author"]),
        content=issue_content,
        frontmatter=Frontmatter(data=frontmatter.data | {
            "id": str(data["id"]),
            "status": str(data["status"]),
            "priority": str(data["priority"]),
            "created": str(data["created"]),
            "completed": str(data["completed"]),
            "assignee": str(data["assignee"]),
            "stage": str(data["stage"]),
            "implementer": str(data["implementer"]),
            "author": str(data["author"]),
            "title": str(data["title"]),
        }, body=body, has_frontmatter=True),
        decode_error=False,
    )


def ensure_not_self_review(
    issue: Issue,
    reviewer: str,
    config: IssuekitConfig | None = None,
) -> None:
    config = config or IssuekitConfig()
    if not config.require_distinct_reviewer:
        return
    if issue.implementer and issue.implementer == reviewer:
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by {issue.implementer}; self-review is not allowed."
        )


def ensure_not_author_self_claim(issue: Issue, assignee: str) -> None:
    if issue.author and issue.author == assignee and issue.assignee == assignee:
        raise WorkflowError(
            f"Issue #{issue.id} was authored by {issue.author}; "
            "author self-implementation is not allowed. Leave the issue unassigned "
            "for the open implement pool or assign a different implementer."
        )


def ensure_assigned_reviewer(
    issue: Issue,
    reviewer_arg: str | None,
    resolved_reviewer: str,
) -> None:
    """Ensure a review transition is performed by the assigned reviewer."""
    if not issue.assignee:
        # Open review pool: any configured agent may decide
        return
    if issue.assignee == resolved_reviewer:
        return
    if reviewer_arg is None:
        raise WorkflowError(
            f"Issue #{issue.id} review is assigned to reviewer "
            f"'{issue.assignee or 'no one'}'. default_reviewer resolved to "
            f"reviewer='{resolved_reviewer}'. Pass reviewer='{issue.assignee}' "
            "or update default_reviewer to match the assigned reviewer."
        )
    raise WorkflowError(
        f"Issue #{issue.id} review is assigned to reviewer "
        f"'{issue.assignee or 'no one'}'. You passed reviewer='{reviewer_arg}'. "
        "Omit `reviewer` to use default_reviewer, or pass the assigned reviewer."
    )


def resolve_reviewer(
    reviewer: str | None,
    config: IssuekitConfig,
    *,
    issue: Issue | None = None,
) -> str:
    configured = (reviewer or config.default_reviewer).strip()
    resolved = (
        _resolve_auto_reviewer(config, issue=issue)
        if configured == AUTO_REVIEWER
        else configured
    )
    if resolved:
        _validate_assignee(resolved, config)
    return resolved


def _resolve_auto_reviewer(config: IssuekitConfig, *, issue: Issue | None) -> str:
    if config.require_distinct_reviewer:
        implementer = issue.implementer if issue is not None else ""
        for assignee in config.assignees:
            if assignee != implementer:
                return assignee
        raise WorkflowError("No reviewer is distinct from the issue implementer.")

    if issue is not None and issue.assignee:
        return issue.assignee
    if config.assignees:
        return config.assignees[0]
    raise WorkflowError("No assignees are configured for auto reviewer.")


def _validate_assignee(value: str, config: IssuekitConfig) -> None:
    if not is_valid_workflow_token(value):
        raise WorkflowError(f"Invalid assignee token: {value}")
    if value not in config.assignees:
        raise WorkflowError(f"Unknown assignee: {value}")


def _validate_stage(value: str, config: IssuekitConfig) -> None:
    if not is_valid_workflow_token(value):
        raise WorkflowError(f"Invalid stage token: {value}")
    if value not in config.stages:
        raise WorkflowError(f"Unknown stage: {value}")


def _validate_ascii_text(value: str, label: str) -> None:
    if has_non_ascii(value):
        raise WorkflowError(f"{label} must be ASCII-only.")


def _handoff_note(*, summary: str, branch: str, commit: str) -> str:
    lines = ["", "## Handoff", "", f"- Summary: {summary}"]
    if branch:
        lines.append(f"- Branch: `{branch}`")
    if commit:
        lines.append(f"- Commit: `{commit}`")
    return "\n".join(lines)


def _review_feedback_note(notes: str) -> str:
    return "\n".join(["", "## Review Feedback", "", f"- {notes}"])
