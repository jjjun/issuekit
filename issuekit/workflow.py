"""Agent handoff workflow transitions."""

from __future__ import annotations

from issuekit.config import IssuekitConfig
from issuekit.core import (
    Issue,
    VALID_ISSUE_PRIORITIES,
    has_non_ascii,
    is_valid_workflow_token,
)


AUTO_REVIEWER = "auto"


class WorkflowError(RuntimeError):
    """Raised when a workflow transition cannot be completed."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def claim_next(
    assignee: str,
    *,
    priority: str | None = None,
    config: IssuekitConfig | None = None,
) -> Issue | None:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("implementing", config)
    if priority is not None and priority not in VALID_ISSUE_PRIORITIES:
        raise WorkflowError(f"Invalid priority: {priority}")
    from issuekit.store import get_store

    store = get_store(config)
    worker = config.worker_key()
    return store.claim_next(assignee=assignee, priority=priority, worker=worker)  # type: ignore[attr-defined]


def claim_issue(
    issue_id: int,
    assignee: str,
    *,
    config: IssuekitConfig | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("implementing", config)
    from issuekit.store import get_store

    store = get_store(config)
    worker = config.worker_key()
    return store.claim_issue(issue_id, assignee=assignee, worker=worker)  # type: ignore[attr-defined]


def submit_for_review(
    issue_id: int,
    *,
    summary: str,
    branch: str | None = None,
    commit: str | None = None,
    assignee: str = "codex",
    reviewer: str | None = None,
    config: IssuekitConfig | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("review", config)
    _validate_ascii_text(summary, "--summary")
    _validate_ascii_text(branch or "", "--branch")
    _validate_ascii_text(commit or "", "--commit")
    from issuekit.store import get_store

    store = get_store(config)
    return store.submit_for_review(  # type: ignore[attr-defined]
        issue_id,
        summary=summary,
        branch=branch,
        commit=commit,
        reviewer=reviewer,
    )


def request_changes(
    issue_id: int,
    *,
    notes: str,
    reviewer: str | None = None,
    assignee: str | None = None,
    config: IssuekitConfig | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    if assignee is not None:
        _validate_assignee(assignee, config)
    _validate_stage("changes_requested", config)
    _validate_ascii_text(notes, "--notes")
    from issuekit.store import get_store

    store = get_store(config)
    worker = config.worker_key()
    return store.request_changes(  # type: ignore[attr-defined]
        issue_id,
        notes=notes,
        reviewer=reviewer,
        assignee=assignee,
        worker=worker,
    )


def find_for(
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

    return get_store(config).find_for(assignee, stage)


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
