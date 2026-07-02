"""Shared issue tracker primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


VALID_ISSUE_STATUSES = {"active", "planned", "investigating", "in_progress", "completed"}
VALID_ISSUE_PRIORITIES = {"high", "medium", "low"}
WORKFLOW_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MOJIBAKE_PATTERN = re.compile(
    "\u7e67|\u7e3a|\u8b41|\u8373|\u87b3|\u8708|\u9ae2|\ufffd"
)
NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7F]")


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
    metadata: dict[str, str]
    worker: str = ""


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
    return data


def _drop_none(values: Mapping[str, Any]) -> dict[str, Any]:
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


def has_mojibake(text: str) -> bool:
    return bool(MOJIBAKE_PATTERN.search(text))


def has_non_ascii(text: str) -> bool:
    return bool(NON_ASCII_PATTERN.search(text))


def is_valid_workflow_token(value: str) -> bool:
    return value == "" or bool(WORKFLOW_TOKEN_PATTERN.fullmatch(value))
