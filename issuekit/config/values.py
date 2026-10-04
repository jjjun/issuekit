"""Scalar conversion and validation helpers for configuration values."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar

from issuekit.core import is_valid_workflow_token
from issuekit.encoding import has_non_ascii

_T = TypeVar("_T")


def _config_value(name: str, value: object, cast: Callable[[object], _T]) -> _T:
    try:
        return cast(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: {exc}") from exc


def _environment_value(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return tuple(str(value).split()) if isinstance(value, str) else ()


def _dedupe_tokens(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _validate_project(project: str) -> None:
    if not project or not is_valid_workflow_token(project):
        raise ValueError(f"Invalid project token: {project}")


def _validate_work_branch(work_branch: str) -> None:
    if not work_branch:
        return
    components = work_branch.split("/")
    invalid = (
        has_non_ascii(work_branch)
        or work_branch.startswith("-")
        or work_branch == "@"
        or ".." in work_branch
        or "@{" in work_branch
        or "//" in work_branch
        or work_branch.startswith("/")
        or work_branch.endswith("/")
        or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "~^:?*[\\"
            for char in work_branch
        )
        or any(
            component.startswith(".") or component.endswith(".") or component.endswith(".lock")
            for component in components
        )
    )
    if invalid:
        raise ValueError(f"Invalid work_branch: {work_branch} is not a valid git branch name.")


def _validate_claim_sync_interval(value: float) -> None:
    if value < 0:
        raise ValueError("claim_sync_interval_sec must be zero or greater.")


def _validate_worker_heartbeat_interval(value: float) -> None:
    if value <= 0:
        raise ValueError("worker_heartbeat_interval_sec must be greater than zero.")


def parse_bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean config value: {value}")


def _bool_value(value: object) -> bool:
    return parse_bool_value(value)
