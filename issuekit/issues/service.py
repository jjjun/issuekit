"""Issue lifecycle operations shared by commands and integrations."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from issuekit.config import IssuekitConfig
from issuekit.core import VALID_ISSUE_PRIORITIES, Issue
from issuekit.errors import WorkflowError
from issuekit.inputs import active_issue_not_found, require_ascii, resolve_text
from issuekit.issues.dependencies import dependency_refs_or_workflow_error
from issuekit.store import managed_issue_store
from issuekit.workers.addressing import target_worker_repo_id, validate_target_worker
from issuekit.workflow import (
    ensure_assigned_reviewer,
    resolve_reviewer,
    resolve_session,
    validate_assignee,
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


def complete_issue(
    issue_id: int,
    *,
    summary: str = "",
    verification: str = "",
    force: bool = False,
    config: IssuekitConfig | None = None,
    store=None,
    agent_model: str | None = None,
    agent_reasoning_effort: str | None = None,
) -> Issue:
    require_ascii(
        summary,
        verification,
        message="--summary and --verification must be ASCII-only.",
    )

    config = config or IssuekitConfig()
    with managed_issue_store(config, store) as active_store:
        return active_store.complete_issue(  # type: ignore[attr-defined]
            issue_id,
            summary=summary,
            verification=verification,
            force=force,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
        )


def edit_issue(
    issue_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    body_file: str | None = None,
    append: str | None = None,
    append_file: str | None = None,
    priority: str | None = None,
    depends_on: str | Sequence[str] | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
    store=None,
) -> Issue:
    _validate_edit_input(
        title=title,
        body=body,
        body_file=body_file,
        append=append,
        append_file=append_file,
        priority=priority,
        depends_on=depends_on,
    )
    config = config or IssuekitConfig()

    with managed_issue_store(config, store) as active_store:
        existing = active_store.get_issue(issue_id)
        if existing is None:
            raise ValueError(active_issue_not_found(issue_id))
        if existing.issue_status == "completed":
            raise WorkflowError(f"Issue #{issue_id} is completed and cannot be edited.")
        stage = existing.stage or "todo"
        if stage not in {"todo", "planned"} and not force:
            raise WorkflowError(
                f"Issue #{issue_id} is at stage {stage}; "
                "pass --force to edit an issue that is already in flight."
            )

        update_body = _body_update(
            stored_body=lambda: active_store.get_issue_edit_body(issue_id),
            body=body,
            body_file=body_file,
            append=append,
            append_file=append_file,
        )
        return active_store.update_issue(
            issue_id,
            title=title.strip() if title is not None else None,
            body=update_body,
            priority=priority,
            depends_on=(
                dependency_refs_or_workflow_error(depends_on)
                if depends_on is not None
                else None
            ),
        )


def _validate_edit_input(
    *,
    title: str | None,
    body: str | None,
    body_file: str | None,
    append: str | None,
    append_file: str | None,
    priority: str | None,
    depends_on: str | Sequence[str] | None,
) -> None:
    body_modes = [value is not None for value in (body, body_file, append, append_file)]
    if sum(body_modes) > 1:
        raise ValueError("Pass only one of --body, --body-file, --append, or --append-file.")
    if title is None and not any(body_modes) and priority is None and depends_on is None:
        raise ValueError(
            "At least one of --title, --body, --body-file, --append, "
            "--append-file, --priority, or --depends-on is required."
        )
    if title is not None:
        if not title.strip():
            raise ValueError("--title is required.")
        require_ascii(title, message="--title must be ASCII-only.")
    if priority is not None and priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}")
    if depends_on is not None:
        dependency_refs_or_workflow_error(depends_on)


def _body_update(
    *,
    stored_body: Callable[[], str],
    body: str | None,
    body_file: str | None,
    append: str | None,
    append_file: str | None,
) -> str | None:
    update_body = resolve_text(body, body_file)
    if update_body is not None:
        require_ascii(update_body, message="--body and --body-file must be ASCII-only.")
        return update_body
    append_body = resolve_text(append, append_file)
    if append_body is not None:
        require_ascii(append_body, message="--append and --append-file must be ASCII-only.")
        return f"{stored_body()}\n\n{append_body}"
    return None


def dispatch_issue(
    issue_id: int,
    *,
    target_worker: str,
    assignee: str | None = None,
    stage: str | None = None,
    allow_unregistered_worker: bool = False,
    config: IssuekitConfig | None = None,
    store=None,
) -> Issue:
    config = config or IssuekitConfig()
    if stage is not None and stage not in {"todo", "planned"}:
        raise WorkflowError(
            "Dispatch stage must be todo or planned.",
            code="invalid_stage",
        )
    with managed_issue_store(config, store) as active_store:
        workers = active_store.list_workers(
            repo_id=target_worker_repo_id(target_worker),
            project=config.project,
        )
        validated_target = validate_target_worker(
            target_worker,
            config=config,
            workers=workers,
            allow_unregistered=allow_unregistered_worker,
        )
        return active_store.dispatch_issue(
            issue_id,
            target_worker=validated_target,
            assignee=assignee,
            stage=stage,
        )
