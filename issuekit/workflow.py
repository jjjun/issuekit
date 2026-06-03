"""Agent handoff workflow transitions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

from issuekit.config import IssuekitConfig
from issuekit.core import (
    Issue,
    VALID_ISSUE_PRIORITIES,
    format_issue_frontmatter,
    has_non_ascii,
    is_valid_workflow_token,
    parse_issue_frontmatter,
    read_issues,
    write_issue_atomic,
)


READY_STAGES = {"", "todo", "changes_requested"}
CLAIMABLE_STATUSES = {"active", "in_progress"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
LOCK_FILE_NAME = ".issuekit-claim.lock"
AUTO_REVIEWER = "auto"


class WorkflowError(RuntimeError):
    """Raised when a workflow transition cannot be completed."""


class WorkflowLockTimeout(TimeoutError):
    """Raised when a workflow lock cannot be acquired before timeout."""


@contextmanager
def claim_lock(
    active_dir: Path | str,
    *,
    timeout: float = 10.0,
    stale_after: float = 60.0,
) -> Iterator[Path]:
    active_path = Path(active_dir)
    active_path.mkdir(parents=True, exist_ok=True)
    lock_path = active_path / LOCK_FILE_NAME
    deadline = time.monotonic() + timeout
    owns_lock = False

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _is_stale_lock(lock_path, stale_after=stale_after):
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise WorkflowLockTimeout(f"Timed out waiting for workflow lock: {lock_path}")
            time.sleep(0.05)
            continue

        payload = json.dumps({"pid": os.getpid(), "ts": time.time()}, sort_keys=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
        owns_lock = True
        break

    try:
        yield lock_path
    finally:
        if owns_lock:
            lock_path.unlink(missing_ok=True)


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

    issues_path = Path(issues_dir)
    with claim_lock(issues_path / "active", timeout=timeout):
        issues = read_issues(issues_path, "active")
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
        issue = sorted(candidates, key=lambda item: (PRIORITY_RANK.get(item.priority, 99), item.id or 0))[
            0
        ]
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

    issues_path = Path(issues_dir)
    with claim_lock(issues_path / "active", timeout=timeout):
        issue = _find_active_issue(issues_path, issue_id)
        if issue.assignee != assignee:
            raise WorkflowError(
                f"Issue #{issue_id} is assigned to {issue.assignee or 'no one'}, not {assignee}."
            )
        reviewer = resolve_reviewer(reviewer, config, issue=issue)
        ensure_not_self_review(issue, reviewer, config)
        note = _handoff_note(summary=summary, branch=branch or "", commit=commit or "")
        return _write_active_issue(
            issues_path,
            issue,
            assignee=reviewer,
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

    issues_path = Path(issues_dir)
    with claim_lock(issues_path / "active", timeout=timeout):
        issue = _find_active_issue(issues_path, issue_id)
        reviewer = resolve_reviewer(reviewer, config, issue=issue)
        if issue.assignee != reviewer:
            raise WorkflowError(
                f"Issue #{issue_id} is assigned to {issue.assignee or 'no one'}, not {reviewer}."
            )
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

    issues = read_issues(issues_dir, "active")
    return [
        issue
        for issue in issues
        if not issue.decode_error
        and (assignee is None or issue.assignee == assignee)
        and (stage is None or issue.stage == stage)
    ]


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
        **_passthrough_frontmatter(frontmatter.data),
        "id": issue.id,
        "status": status or issue.issue_status or issue.status,
        "priority": issue.priority or "medium",
        "created": issue.created,
        "completed": issue.completed,
        "assignee": issue.assignee if assignee is None else assignee,
        "stage": issue.stage if stage is None else stage,
        "implementer": issue.implementer if implementer is None else implementer,
        "title": issue.title,
    }
    body = frontmatter.body.strip("\n")
    if extra_body:
        body = f"{body}\n{extra_body.rstrip()}"
    write_issue_atomic(issue.file_path, f"{format_issue_frontmatter(data)}{body}\n")
    return _find_active_issue(issues_dir, issue.id or 0)


def _passthrough_frontmatter(data: dict[str, str]) -> dict[str, str]:
    managed_keys = {
        "id",
        "status",
        "priority",
        "created",
        "completed",
        "assignee",
        "stage",
        "implementer",
        "title",
    }
    return {key: value for key, value in data.items() if key not in managed_keys}


def _find_active_issue(issues_dir: Path, issue_id: int) -> Issue:
    issue = next((candidate for candidate in read_issues(issues_dir, "active") if candidate.id == issue_id), None)
    if issue is None:
        raise WorkflowError(f"Active issue #{issue_id} was not found.")
    if issue.decode_error:
        raise WorkflowError(f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}")
    return issue


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


def _is_stale_lock(lock_path: Path, *, stale_after: float) -> bool:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8-sig"))
        ts = float(payload.get("ts", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return time.time() - lock_path.stat().st_mtime > stale_after
    return time.time() - ts > stale_after
