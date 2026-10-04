"""Shared issue tracker primitives."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

VALID_ISSUE_PRIORITIES = {"high", "medium", "low"}
WORKFLOW_TOKEN_MAX_LEN = 32
WORKFLOW_TOKEN_PATTERN = re.compile(
    rf"^[a-z0-9][a-z0-9_-]{{0,{WORKFLOW_TOKEN_MAX_LEN - 1}}}$"
)


@dataclass(frozen=True)
class Issue:
    id: int | None
    ref: str
    title: str
    issue_status: str
    created: str
    completed: str
    priority: str
    assignee: str
    stage: str
    implementer: str
    author: str
    body: str
    metadata: dict[str, Any]
    worker: str = ""
    target_worker: str = ""
    depends_on: tuple[str, ...] = ()
    dependencies: tuple[dict[str, object], ...] = ()
    dependency_state: str = ""
    warning: str = ""


@dataclass(frozen=True)
class TargetAddress:
    repo: str
    worker: str = ""
    machine: str = ""

    @property
    def directed_worker(self) -> str:
        """Return the worker token to send as a directed target."""
        if self.worker and self.machine:
            return f"{self.worker}@{self.machine}"
        return self.worker


def issue_dict(issue: Issue, *, include_body: bool = False) -> dict[str, object]:
    """Serialize an issue for JSON output.

    Shared by the MCP server and the CLI so both paths emit identical payloads.
    """
    data: dict[str, object] = {
        "id": issue.id,
        "title": issue.title,
        "status": issue.issue_status,
        "assignee": issue.assignee,
        "stage": issue.stage,
        "priority": issue.priority,
        "implementer": issue.implementer,
        "author": issue.author,
        "ref": issue.ref,
    }
    if include_body:
        data["body"] = issue.body
    if issue.worker:
        data["worker"] = issue.worker
    if issue.target_worker:
        data["target_worker"] = issue.target_worker
    if issue.depends_on:
        data["depends_on"] = list(issue.depends_on)
    if issue.dependency_state:
        data["dependency_state"] = issue.dependency_state
    if issue.dependencies:
        data["dependencies"] = [dict(item) for item in issue.dependencies]
    if issue.warning:
        data["warning"] = issue.warning
    for key in ("author_session", "implementer_session", "reviewer_session"):
        value = issue.metadata.get(key)
        if value:
            data[key] = value
    return data


def drop_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def parse_issue_id_arg(raw_issue_id: str) -> int:
    try:
        return int(raw_issue_id)
    except ValueError as exc:
        raise ValueError(f"Invalid issue id: {raw_issue_id}") from exc


def get_issue_heading(content: str) -> re.Match[str] | None:
    return re.search(r"^#\s+Issue\s+#\d+:\s*(.+)$", content, re.MULTILINE) or re.search(
        r"^#\s+(.+)$", content, re.MULTILINE
    )


def is_valid_workflow_token(value: str) -> bool:
    return value == "" or bool(WORKFLOW_TOKEN_PATTERN.fullmatch(value))


def worker_key(repo_id: str, worker_name: str) -> str:
    """Return the current worker key format used by API lifecycle calls."""
    return f"{worker_name}.{repo_id}"


def qualified_worker_key(machine_id: str, repo_id: str, worker_name: str) -> str:
    """Return the machine-qualified worker.repo@machine key format."""
    return f"{worker_name}.{repo_id}@{machine_id}"


def worker_display_from_parts(repo_id: str, worker_name: str) -> str:
    return worker_key(repo_id, worker_name)


def worker_display_from_row(row: Mapping[str, object]) -> str:
    repo_id = _string(row.get("repo_id"))
    worker_name = _worker_name(row)
    if repo_id and worker_name:
        return worker_display_from_parts(repo_id, worker_name)
    return "?.?"


def worker_keys_from_row(row: Mapping[str, object]) -> set[str]:
    keys: set[str] = set()
    machine_id = _string(row.get("machine_id"))
    repo_id = _string(row.get("repo_id"))
    worker_name = _worker_name(row)
    if repo_id and worker_name:
        keys.add(worker_key(repo_id, worker_name))
        if machine_id:
            keys.add(qualified_worker_key(machine_id, repo_id, worker_name))
    return keys


def worker_keys_match(
    left: str,
    right: str,
    *,
    require_target_machine: bool = False,
) -> bool:
    """Return True when two worker keys refer to the same worker identity.

    Machine ids discriminate only when both keys carry the machine-qualified
    ``@machine`` suffix. Bare ``worker.repo`` keys stay machine-agnostic unless
    ``require_target_machine`` is set for a directed target.
    """
    if left == right:
        return True
    left_parts = _worker_key_parts(left)
    right_parts = _worker_key_parts(right)
    if left_parts is None or right_parts is None:
        return False
    return _worker_key_parts_match(
        left_parts,
        right_parts,
        require_target_machine=require_target_machine,
    )


def worker_key_matches_row(
    key: str,
    row: Mapping[str, object],
    *,
    directed_target: bool = False,
) -> bool:
    """Return whether a worker key matches a registered worker row.

    Qualified keys compare against the row's qualified key when it has a
    machine id, so the row's bare compatibility key cannot mask a mismatch.
    """
    row_keys = worker_keys_from_row(row)
    if "@" in key:
        qualified_keys = {candidate for candidate in row_keys if "@" in candidate}
        if qualified_keys:
            row_keys = qualified_keys
    return any(
        worker_keys_match(key, candidate, require_target_machine=directed_target)
        for candidate in row_keys
    )


@dataclass(frozen=True)
class _WorkerKeyParts:
    worker_name: str
    repo_id: str | None
    machine_id: str | None


def _worker_key_parts_match(
    target: _WorkerKeyParts,
    other: _WorkerKeyParts,
    *,
    require_target_machine: bool,
) -> bool:
    if target.worker_name != other.worker_name:
        return False
    if target.repo_id and other.repo_id and target.repo_id != other.repo_id:
        return False
    if target.machine_id and other.machine_id and target.machine_id != other.machine_id:
        return False
    if require_target_machine and target.machine_id and not other.machine_id:
        return False
    return True


def _worker_name(row: Mapping[str, object]) -> str:
    return _string(row.get("worker_name"))


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _worker_key_parts(value: str) -> _WorkerKeyParts | None:
    text = value.strip()
    if not text:
        return None
    left, separator, machine_id = text.rpartition("@")
    if not separator:
        left, machine_id = text, ""
    elif not left or not machine_id:
        return None
    if "." in left:
        worker_name, repo_id = left.rsplit(".", 1)
        if not worker_name or not repo_id:
            return None
        return _WorkerKeyParts(
            worker_name=worker_name,
            repo_id=repo_id,
            machine_id=machine_id or None,
        )
    if machine_id:
        return _WorkerKeyParts(worker_name=left, repo_id=None, machine_id=machine_id)
    # A bare worker name carries no repo or machine context; only exact string
    # equality identifies it, matching the pre-qualified behavior.
    return None


def parse_target_address(value: str, *, label: str = "target") -> TargetAddress:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    text, separator, machine = text.partition("@")
    if separator and not machine:
        raise ValueError(
            f"Invalid {label} address: {value}. Expected worker.repo@machine."
        )
    if machine and not is_valid_workflow_token(machine):
        raise ValueError(f"Invalid {label} machine token: {machine}")
    if "." not in text:
        if machine:
            raise ValueError(
                f"Invalid {label} address: {value}. "
                "A machine qualifier requires the worker.repo@machine form."
            )
        if not is_valid_workflow_token(text):
            raise ValueError(f"Invalid {label} token: {value}")
        return TargetAddress(repo=text)

    worker, repo = text.split(".", 1)
    if not worker or not repo or "." in repo:
        raise ValueError(
            f"Invalid {label} address: {value}. "
            "Expected repo, worker.repo, or worker.repo@machine."
        )
    if not is_valid_workflow_token(worker):
        raise ValueError(f"Invalid {label} worker token: {worker}")
    if not is_valid_workflow_token(repo):
        raise ValueError(f"Invalid {label} repo token: {repo}")
    return TargetAddress(repo=repo, worker=worker, machine=machine)
