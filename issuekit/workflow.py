"""Agent handoff workflow transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from issuekit.config import IssuekitConfig
from issuekit.core import (
    VALID_ISSUE_PRIORITIES,
    Issue,
    is_valid_workflow_token,
    worker_keys_match,
)
from issuekit.encoding import ASCII_ONLY_HINT, has_non_ascii
from issuekit.gitutil import git_current_branch
from issuekit.guards.author import (
    AuthorOrchestrationContext,
    author_handoff_enforced,
    enforce_no_author_guard,
)
from issuekit.guards.branch import enforce_work_branch
from issuekit.guards.claim_sync import enforce_claim_sync
from issuekit.issues.session import current_session_token, validate_session_token

AUTO_REVIEWER = "auto"


@dataclass(frozen=True)
class ReclaimResult:
    previous: Issue
    issue: Issue
    reason: str | None
    expected_worker: str | None
    actor: str
    audit_reason: str | None


@dataclass(frozen=True)
class ReaddressResult:
    previous: Issue
    issue: Issue
    expected_target_worker: str
    actor: str
    audit_reason: str | None


class WorkflowError(RuntimeError):
    """Raised when a workflow transition cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def __str__(self) -> str:
        message = super().__str__()
        from issuekit.guards.separation import separation_guard_note

        note = separation_guard_note(message, code=self.code)
        if note is None or note in message:
            return message
        return f"{message}\n{note}"


def claim_next(
    assignee: str,
    *,
    priority: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
    cwd: str = ".",
    allow_author_guard_override: bool = False,
    allow_any_branch: bool = False,
    no_sync: bool = False,
    session: str | None = None,
) -> Issue | None:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("implementing", config)
    if priority is not None and priority not in VALID_ISSUE_PRIORITIES:
        raise WorkflowError(f"Invalid priority: {priority}")
    enforce_no_author_guard(
        cwd=cwd,
        config=config,
        action="claim-next",
        allow_override=allow_author_guard_override,
    )
    enforce_work_branch(
        cwd,
        config=config,
        action="claim-next",
        allow_any_branch=allow_any_branch,
    )
    enforce_claim_sync(
        cwd,
        config=config,
        action="claim-next",
        no_sync=no_sync,
    )

    with _managed_store(config, store) as active_store:
        worker = config.qualified_worker_key()
        resolved_session = _resolve_session(session)
        return active_store.claim_next(  # type: ignore[attr-defined]
            assignee=assignee,
            priority=priority,
            worker=worker,
            allow_self_implement=not author_handoff_enforced(),
            session=resolved_session,
        )


def claim_issue(
    issue_id: int,
    assignee: str,
    *,
    config: IssuekitConfig | None = None,
    store=None,
    cwd: str = ".",
    allow_author_guard_override: bool = False,
    allow_any_branch: bool = False,
    no_sync: bool = False,
    session: str | None = None,
    orchestration: AuthorOrchestrationContext | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_assignee(assignee, config)
    _validate_stage("implementing", config)
    enforce_no_author_guard(
        cwd=cwd,
        config=config,
        action=f"claim issue #{issue_id}",
        issue_id=issue_id,
        allow_override=allow_author_guard_override,
        orchestration=orchestration,
    )
    enforce_work_branch(
        cwd,
        config=config,
        action=f"claim issue #{issue_id}",
        allow_any_branch=allow_any_branch,
    )

    with _managed_store(config, store) as active_store:
        previous_issue = active_store.get_issue(issue_id)
        if not _is_same_worker_changes_continuation(previous_issue, config):
            enforce_claim_sync(
                cwd,
                config=config,
                action=f"claim issue #{issue_id}",
                no_sync=no_sync,
            )
        worker = config.qualified_worker_key()
        resolved_session = _resolve_session(session)
        _ensure_orchestration_session(orchestration, resolved_session)
        return active_store.claim_issue(  # type: ignore[attr-defined]
            issue_id,
            assignee=assignee,
            worker=worker,
            allow_self_implement=not author_handoff_enforced() and orchestration is None,
            session=resolved_session,
        )


def reclaim_issue(
    issue_id: int,
    *,
    force: bool = False,
    stale_after_sec: float | None = None,
    reason: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
) -> ReclaimResult:
    config = config or IssuekitConfig()
    _validate_stage("implementing", config)
    if reason is not None:
        _validate_ascii_text(reason, "--reason")

    with _managed_store(config, store) as active_store:
        previous = active_store.get_issue(issue_id)
        if previous is None:
            raise WorkflowError(f"Issue #{issue_id} was not found.", code="not_found")
        if previous.stage != "implementing":
            raise WorkflowError(
                f"Issue #{issue_id} is at stage {previous.stage or 'todo'}, not implementing.",
                code="invalid_transition",
            )

        claim = None
        if not force:
            from issuekit.issues.orphans import DEFAULT_STALE_AFTER_SEC, list_stale_claims

            claims = list_stale_claims(
                config,
                stale_after_sec=(
                    DEFAULT_STALE_AFTER_SEC
                    if stale_after_sec is None
                    else stale_after_sec
                ),
            )
            claim = next((item for item in claims if item.issue.id == issue_id), None)
            if claim is None:
                raise WorkflowError(
                    f"Issue #{issue_id} is not currently flagged as an orphaned or stale claim; "
                    "use --force for human emergency recovery.",
                    code="not_stale",
                )

        expected_worker = claim.worker if claim is not None else previous.worker or None
        actor = _reclaim_actor(config)
        issue = active_store.reclaim_issue(  # type: ignore[attr-defined]
            issue_id,
            expected_worker=expected_worker,
            actor=actor,
            reason=reason,
        )
        return ReclaimResult(
            previous=previous,
            issue=issue,
            reason=claim.reason if claim is not None else None,
            expected_worker=expected_worker,
            actor=actor,
            audit_reason=reason,
        )


def readdress_issue(
    issue_id: int,
    *,
    reason: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
) -> ReaddressResult:
    config = config or IssuekitConfig()
    if reason is not None:
        _validate_ascii_text(reason, "--reason")

    with _managed_store(config, store) as active_store:
        previous = active_store.get_issue(issue_id)
        if previous is None:
            raise WorkflowError(f"Issue #{issue_id} was not found.", code="not_found")
        target_worker = previous.target_worker
        if not target_worker:
            raise WorkflowError(
                f"Issue #{issue_id} is not directed to a worker.",
                code="invalid_transition",
            )
        actor = _reclaim_actor(config)
        issue = active_store.readdress_issue(  # type: ignore[attr-defined]
            issue_id,
            expected_target_worker=target_worker,
            actor=actor,
            reason=reason,
        )
        return ReaddressResult(
            previous=previous,
            issue=issue,
            expected_target_worker=target_worker,
            actor=actor,
            audit_reason=reason,
        )


def submit_for_review(
    issue_id: int,
    *,
    summary: str,
    branch: str | None = None,
    commit: str | None = None,
    reviewer: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
    cwd: str = ".",
    allow_author_guard_override: bool = False,
    allow_any_branch: bool = False,
    session: str | None = None,
    orchestration: AuthorOrchestrationContext | None = None,
    agent_model: str | None = None,
    agent_reasoning_effort: str | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_stage("review", config)
    if branch is None:
        branch = git_current_branch(cwd)
    _validate_ascii_text(summary, "--summary")
    _validate_ascii_text(branch or "", "--branch")
    _validate_ascii_text(commit or "", "--commit")
    enforce_no_author_guard(
        cwd=cwd,
        config=config,
        action=f"submit issue #{issue_id} for review",
        issue_id=issue_id,
        allow_override=allow_author_guard_override,
        orchestration=orchestration,
    )
    enforce_work_branch(
        cwd,
        config=config,
        action=f"submit issue #{issue_id} for review",
        allow_any_branch=allow_any_branch,
    )
    with _managed_store(config, store) as active_store:
        resolved_session = _resolve_session(session)
        _ensure_orchestration_session(orchestration, resolved_session)
        return active_store.submit_for_review(  # type: ignore[attr-defined]
            issue_id,
            summary=summary,
            branch=branch,
            commit=commit,
            reviewer=reviewer,
            session=resolved_session,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
        )


def request_changes(
    issue_id: int,
    *,
    notes: str,
    reviewer: str | None = None,
    assignee: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
    session: str | None = None,
    agent_model: str | None = None,
    agent_reasoning_effort: str | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    if assignee is not None:
        _validate_assignee(assignee, config)
    _validate_stage("changes_requested", config)
    _validate_ascii_text(notes, "--notes")
    with _managed_store(config, store) as active_store:
        worker = config.worker_key()
        resolved_session = _resolve_session(session)
        return active_store.request_changes(  # type: ignore[attr-defined]
            issue_id,
            notes=notes,
            reviewer=reviewer,
            assignee=assignee,
            worker=worker,
            session=resolved_session,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
        )


def next_review(
    reviewer: str | None = None,
    *,
    config: IssuekitConfig | None = None,
    store=None,
    include_open: bool = False,
) -> Issue | None:
    """Return the next issue waiting for a reviewer."""
    config = config or IssuekitConfig()
    with _managed_store(config, store) as active_store:
        if reviewer is None and config.default_reviewer == AUTO_REVIEWER:
            issues = active_store.find_for(None, "review")  # type: ignore[attr-defined]
            return issues[0] if issues else None

        resolved = resolve_reviewer(reviewer, config)
        issues = active_store.find_for(resolved, "review")  # type: ignore[attr-defined]
        if include_open:
            open_issues = active_store.find_for(None, "review")  # type: ignore[attr-defined]
            issues.extend(issue for issue in open_issues if not issue.assignee)
            issues = sorted(
                {issue.id or 0: issue for issue in issues}.values(),
                key=lambda issue: (issue.id or 0, issue.ref),
            )
        return issues[0] if issues else None


def find_for(
    assignee: str | None = None,
    *,
    stage: str | None = None,
    config: IssuekitConfig | None = None,
    store=None,
) -> list[Issue]:
    config = config or IssuekitConfig()
    if assignee:
        _validate_assignee(assignee, config)
    if stage:
        _validate_stage(stage, config)

    with _managed_store(config, store) as active_store:
        return active_store.find_for(assignee, stage)


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


def resolve_implementer(
    implementer: str | None,
    config: IssuekitConfig,
) -> str | None:
    resolved = (implementer or config.default_implementer).strip()
    if resolved:
        _validate_assignee(resolved, config)
        return resolved
    if len(config.assignees) == 1:
        return config.assignees[0]
    return None


def _resolve_auto_reviewer(config: IssuekitConfig, *, issue: Issue | None) -> str:
    if config.require_distinct_reviewer:
        implementer = issue.implementer if issue is not None else ""
        for assignee in config.assignees:
            if assignee != implementer:
                return assignee
        raise WorkflowError(
            "Distinct-reviewer guard (require_distinct_reviewer) blocks auto reviewer "
            "resolution: no configured reviewer is distinct from the issue implementer. "
            "Recovery: configure an assignee distinct from issue.implementer. In "
            "non-API mode only, set require_distinct_reviewer = false if local policy "
            "permits.",
            code="distinct_reviewer_guard",
        )

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
        raise WorkflowError(f"{label} must be ASCII-only. {ASCII_ONLY_HINT}")


def _reclaim_actor(config: IssuekitConfig) -> str:
    return config.worker_key() or "issuekit"


def _managed_store(config: IssuekitConfig, store):
    # Local import: issuekit.store imports WorkflowError from this module.
    from issuekit.store import managed_issue_store

    return managed_issue_store(config, store)


def _is_same_worker_changes_continuation(
    issue: Issue | None,
    config: IssuekitConfig,
) -> bool:
    if issue is None or issue.stage != "changes_requested":
        return False
    current_worker = config.qualified_worker_key()
    if not current_worker:
        return False
    implementation_worker = issue.worker or str(
        issue.metadata.get("implementation_worker") or ""
    ).strip()
    return bool(
        implementation_worker
        and worker_keys_match(implementation_worker, current_worker)
    )


def _resolve_session(explicit: str | None) -> str | None:
    try:
        if explicit is not None:
            return validate_session_token(explicit)
        return current_session_token()
    except ValueError as exc:
        raise WorkflowError(str(exc), code="invalid_session") from exc


def _ensure_orchestration_session(
    orchestration: AuthorOrchestrationContext | None,
    session: str | None,
) -> None:
    if orchestration is None:
        return
    if session == orchestration.run_session:
        return
    raise WorkflowError(
        "Orchestrated lifecycle calls must use the launched run session.",
        code="invalid_session",
    )
