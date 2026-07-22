"""Shared issue tracker primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


VALID_ISSUE_PRIORITIES = {"high", "medium", "low"}
WORKFLOW_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
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


def issue_dict(issue: "Issue", *, include_body: bool = False) -> dict[str, object]:
    """Serialize an issue for JSON output.

    Shared by the MCP server and the CLI so both paths emit identical payloads.
    """
    data: dict[str, object] = {
        "id": issue.id,
        "title": issue.title,
        "status": issue.issue_status,
        "assignee": issue.assignee,
        "stage": issue.stage,
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


def _drop_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def last_nonempty_line(text: str) -> str | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


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
