"""Machine-local guard for author sessions that must stop after authoring."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from issuekit.config import IssuekitConfig, parse_bool_value
from issuekit.config.local import LocalConfigError, read_local_config, write_local_config

STOP_SENTINEL = "STOP_NOW"
REQUIRED_NEXT_ACTION = "STOP"
ENFORCE_AUTHOR_HANDOFF_ENV = "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF"


@dataclass(frozen=True)
class AuthorGuard:
    project: str
    kind: str
    id: str
    ref: str
    target_project: str
    author_agent: str
    author_session: str
    worker: str
    created: str
    required_next_action: str = REQUIRED_NEXT_ACTION

    def to_dict(self) -> dict[str, str]:
        data = {
            "project": self.project,
            "kind": self.kind,
            "id": self.id,
            "ref": self.ref,
            "author_agent": self.author_agent,
            "author_session": self.author_session,
            "created": self.created,
            "required_next_action": self.required_next_action,
        }
        if self.target_project:
            data["target_project"] = self.target_project
        if self.worker:
            data["worker"] = self.worker
        return data

    @property
    def label(self) -> str:
        target = f" in {self.target_project}" if self.target_project else ""
        return f"{self.kind} {self.ref or self.id}{target}"


def create_author_guard(
    cwd: Path | str,
    *,
    config: IssuekitConfig,
    kind: str,
    item_id: int | str | None,
    ref: str,
    author_agent: str | None,
    author_session: str | None = None,
    target_project: str | None = None,
) -> AuthorGuard:
    guard = AuthorGuard(
        project=config.project,
        kind=kind,
        id="" if item_id is None else str(item_id),
        ref=ref,
        target_project=target_project or "",
        author_agent=(author_agent or "unknown").strip() or "unknown",
        author_session=(author_session or "").strip(),
        worker=config.worker_key() or "",
        created=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )
    local_config = read_local_config(cwd)
    write_local_config(
        cwd,
        worker=local_config.worker,
        refs=local_config.refs,
        author_guards=_with_author_guard(local_config.author_guards, guard),
    )
    return guard


def read_author_guards(cwd: Path | str = ".") -> tuple[AuthorGuard, ...]:
    try:
        raw_guards = read_local_config(cwd).author_guards
    except LocalConfigError as exc:
        from issuekit.workflow import WorkflowError

        raise WorkflowError(str(exc)) from exc
    return tuple(
        guard
        for raw in raw_guards
        if (guard := _guard_from_mapping(raw)) is not None
    )


def clear_author_guard(cwd: Path | str = ".", *, ref: str | None = None) -> bool:
    local_config = read_local_config(cwd)
    guards = local_config.author_guards
    remaining = (
        ()
        if ref is None
        else tuple(guard for guard in guards if str(guard.get("ref", "")) != ref)
    )
    if len(remaining) == len(guards):
        return False
    write_local_config(
        cwd,
        worker=local_config.worker,
        refs=local_config.refs,
        author_guards=remaining,
    )
    return True


def guard_dict(guard: AuthorGuard | None) -> dict[str, str] | None:
    return None if guard is None else guard.to_dict()


def guards_dict(guards: tuple[AuthorGuard, ...]) -> list[dict[str, str]]:
    return [guard.to_dict() for guard in guards]


def stop_message(guard: AuthorGuard) -> str:
    if guard.kind == "proposal":
        return (
            f"Proposal {guard.ref} sent to {guard.target_project}. If this session's "
            "task was only to send the proposal, stop here; otherwise continue "
            "your current task."
        )
    return (
        f"{STOP_SENTINEL}: this checkout authored {guard.label}. "
        "Stop this session before implementing. Recovery: run "
        "`issuekit author-guard clear` after handing off, or pass the explicit "
        "override flag for a human emergency."
    )


def enforce_no_author_guard(
    *,
    cwd: Path | str,
    config: IssuekitConfig,
    action: str,
    issue_id: int | None = None,
    allow_override: bool = False,
    orchestration: AuthorOrchestrationContext | None = None,
) -> None:
    if allow_override:
        return
    if not _enforce_author_handoff():
        return
    guard = next(
        (
            item
            for item in read_author_guards(cwd)
            if item.project == config.project
            and _guard_blocks_issue_lifecycle(item, config=config, issue_id=issue_id)
            and not _orchestration_allows_issue_lifecycle(item, orchestration)
        ),
        None,
    )
    if guard is None:
        return
    from issuekit.workflow import WorkflowError

    detail = _orchestration_rejection_detail(guard, orchestration)
    suffix = f" {detail}" if detail else ""
    raise WorkflowError(
        f"Author-session guard blocks {action}: {stop_message(guard)}{suffix}",
        code="author_session_guard",
    )


def _guard_blocks_issue_lifecycle(
    guard: AuthorGuard,
    *,
    config: IssuekitConfig,
    issue_id: int | None,
) -> bool:
    if guard.kind != "issue":
        return False
    if issue_id is None:
        return True
    target_id = str(issue_id)
    if guard.id:
        return guard.id == target_id
    return guard.ref == f"{config.project}#{target_id}"


@dataclass(frozen=True)
class AuthorOrchestrationContext:
    implementer_agent: str
    run_session: str


def _orchestration_allows_issue_lifecycle(
    guard: AuthorGuard,
    orchestration: AuthorOrchestrationContext | None,
) -> bool:
    if orchestration is None:
        return False
    if guard.author_agent == "unknown":
        return False
    if orchestration.implementer_agent != guard.author_agent:
        return True
    return bool(guard.author_session and orchestration.run_session != guard.author_session)


def _orchestration_rejection_detail(
    guard: AuthorGuard,
    orchestration: AuthorOrchestrationContext | None,
) -> str:
    if orchestration is None:
        return ""
    if guard.author_agent == "unknown":
        return (
            "Orchestrated implement is not allowed because the author agent is "
            "unknown; clear the guard only after handing the issue to a distinct "
            "session."
        )
    if not guard.author_session:
        return (
            "Orchestrated same-agent implement requires the issue to have been "
            "authored with ISSUEKIT_SESSION recorded."
        )
    return (
        "Orchestrated same-agent implement requires a run session distinct from "
        "the recorded author session."
    )


def _guard_from_mapping(raw: Mapping[str, object] | None) -> AuthorGuard | None:
    if raw is None:
        return None
    project = _string(raw.get("project"))
    kind = _string(raw.get("kind"))
    if not project or not kind:
        return None
    return AuthorGuard(
        project=project,
        kind=kind,
        id=_string(raw.get("id")),
        ref=_string(raw.get("ref")),
        target_project=_string(raw.get("target_project")),
        author_agent=_string(raw.get("author_agent")) or "unknown",
        author_session=_string(raw.get("author_session")),
        worker=_string(raw.get("worker")),
        created=_string(raw.get("created")),
        required_next_action=_string(raw.get("required_next_action")) or REQUIRED_NEXT_ACTION,
    )


def _with_author_guard(
    raw_guards: tuple[dict[str, object], ...],
    guard: AuthorGuard,
) -> tuple[dict[str, object], ...]:
    guard_mapping = guard.to_dict()
    key = (guard.kind, guard.ref)
    replaced = False
    guards: list[dict[str, object]] = []
    for existing in raw_guards:
        if (str(existing.get("kind", "")), str(existing.get("ref", ""))) == key:
            if not replaced:
                guards.append(guard_mapping)
            replaced = True
        else:
            guards.append(existing)
    if not replaced:
        guards.append(guard_mapping)
    return tuple(guards)


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def author_handoff_enforced() -> bool:
    """Public accessor for the author-handoff enforcement decision.

    Reused by the claim path so the local author-session guard and the
    server-side author==claimer guard relax together and cannot diverge.
    """
    return _enforce_author_handoff()


def _enforce_author_handoff() -> bool:
    raw = os.getenv(ENFORCE_AUTHOR_HANDOFF_ENV)
    if raw is None or not raw.strip():
        return True
    try:
        return parse_bool_value(raw)
    except ValueError:
        # Fail safe: an unrecognized value keeps the guard enforced rather than
        # crashing every guarded lifecycle command. Only 0/false/no/off disable.
        return True
