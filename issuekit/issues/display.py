"""Human-readable issue display helpers."""

from __future__ import annotations

from collections.abc import Mapping

from issuekit.core import Issue

ATTENTION_DEPENDENCY_STATES = {"waiting", "attention"}


def dependency_marker(issue: Issue) -> str | None:
    if issue.dependency_state in ATTENTION_DEPENDENCY_STATES:
        return f"dependency_state={issue.dependency_state}"
    return None


def dependency_detail_lines(issue: Issue) -> list[str]:
    lines: list[str] = []
    if issue.dependency_state:
        lines.append(f"dependency_state={issue.dependency_state}")
    if issue.dependencies:
        lines.extend(_dependency_row(row) for row in issue.dependencies)
    elif issue.depends_on:
        lines.append("depends_on=" + ",".join(issue.depends_on))
    return lines


def _dependency_row(row: Mapping[str, object]) -> str:
    ref = _text(row, "ref", "depends_on", "dependency", "target_ref") or "-"
    state = _text(row, "state", "dependency_state", "resolution_state") or "-"
    status = (
        _text(row, "status", "issue_status", "target_status")
        or _nested_text(row, "issue", "status")
        or "-"
    )
    stage = _text(row, "stage", "target_stage") or _nested_text(row, "issue", "stage") or "-"
    return f"depends_on={ref} state={state} status={status} stage={stage}"


def _text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _nested_text(row: Mapping[str, object], key: str, nested_key: str) -> str:
    value = row.get(key)
    if not isinstance(value, Mapping):
        return ""
    nested = value.get(nested_key)
    if nested is None:
        return ""
    return str(nested).strip()
