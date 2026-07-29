"""Helpers for issue-level dependency references."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

DEPENDENCY_REF_PATTERN = re.compile(
    r"^[A-Za-z0-9_.-]+#(?:(?:issue|proposal):)?[0-9]+$"
)
DEPENDENCY_REF_EXPECTED = "project#N, project#issue:N, or project#proposal:N"


def dependency_refs(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = _split_dependency_refs(value)
    else:
        raw_items = []
        for item in value:
            raw_items.extend(_split_dependency_refs(str(item)))
    return _dedupe_refs(raw_items)


def _split_dependency_refs(value: str) -> list[str]:
    refs: list[str] = []
    for raw in re.split(r"[\s,]+", value):
        ref = raw.strip().strip(".;")
        if not ref:
            continue
        if not DEPENDENCY_REF_PATTERN.match(ref):
            raise ValueError(
                f"Invalid dependency reference: {ref}. Expected {DEPENDENCY_REF_EXPECTED}."
            )
        refs.append(ref)
    return refs


def _dedupe_refs(refs: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return tuple(deduped)


def bare_ref_collision_warnings(rows: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    warnings: list[str] = []
    for row in rows:
        ref = _dependency_row_text(row, "ref", "depends_on", "dependency", "target_ref")
        if not ref or not _is_bare_dependency_ref(ref):
            continue
        state = _dependency_row_text(row, "state", "dependency_state", "resolution_state")
        if state != "attention":
            continue
        if not (_has_issue_resolution(row) and _has_proposal_resolution(row)):
            continue
        project, raw_number = ref.split("#", 1)
        warnings.append(
            f"Dependency reference {ref} is ambiguous: the API found both an issue "
            f"and a proposal. Use {project}#issue:{raw_number} or "
            f"{project}#proposal:{raw_number}; for pending proposals prefer "
            f"{project}#proposal:{raw_number}."
        )
    return _dedupe_refs(warnings)


def _is_bare_dependency_ref(ref: str) -> bool:
    project, separator, suffix = ref.partition("#")
    return bool(project and separator and suffix.isdigit())


def _has_issue_resolution(row: Mapping[str, object]) -> bool:
    return _has_row_value(
        row,
        "issue",
        "issue_id",
        "issue_number",
        "issue_status",
        "status",
        "target_issue",
        "target_status",
    )


def _has_proposal_resolution(row: Mapping[str, object]) -> bool:
    return _has_row_value(
        row,
        "proposal",
        "proposal_id",
        "proposal_status",
        "target_proposal",
    )


def _has_row_value(row: Mapping[str, object], *keys: str) -> bool:
    return any(_row_value_present(row.get(key)) for key in keys)


def _row_value_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value)
    return True


def _dependency_row_text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
