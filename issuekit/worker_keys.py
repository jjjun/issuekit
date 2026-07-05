"""Worker identity key helpers."""

from __future__ import annotations

from collections.abc import Mapping


def worker_key(repo_id: str, worker_name: str) -> str:
    """Return the current worker key format used by API lifecycle calls."""
    return f"{worker_name}.{repo_id}"


def legacy_worker_key(machine_id: str, repo_id: str, worker_name: str) -> str:
    """Return the legacy machine/repo/worker key format."""
    return f"{machine_id}/{repo_id}/{worker_name}"


def worker_display_from_parts(repo_id: str, worker_name: str) -> str:
    return worker_key(repo_id, worker_name)


def worker_display_from_row(row: Mapping[str, object]) -> str:
    repo_id = _string(row.get("repo_id"))
    worker_name = _worker_name(row)
    if repo_id and worker_name:
        return worker_display_from_parts(repo_id, worker_name)
    legacy = _legacy_from_row(row)
    return legacy or "?.?"


def worker_keys_from_row(row: Mapping[str, object]) -> set[str]:
    keys: set[str] = set()
    repo_id = _string(row.get("repo_id"))
    worker_name = _worker_name(row)
    if repo_id and worker_name:
        keys.add(worker_key(repo_id, worker_name))
    legacy = _legacy_from_row(row)
    if legacy:
        keys.add(legacy)
    row_id = _string(row.get("id"))
    if row_id:
        keys.add(row_id)
    return keys


def worker_keys_match(left: str, right: str) -> bool:
    if left == right:
        return True
    left_pair = _repo_worker_pair(left)
    right_pair = _repo_worker_pair(right)
    return bool(left_pair and left_pair == right_pair)


def _worker_name(row: Mapping[str, object]) -> str:
    return _string(row.get("worker_name") or row.get("worker_id"))


def _legacy_from_row(row: Mapping[str, object]) -> str:
    machine_id = _string(row.get("machine_id"))
    repo_id = _string(row.get("repo_id"))
    worker_name = _worker_name(row)
    if machine_id and repo_id and worker_name:
        return legacy_worker_key(machine_id, repo_id, worker_name)
    return ""


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _repo_worker_pair(value: str) -> tuple[str, str] | None:
    text = value.strip()
    if not text:
        return None
    parts = text.split("/")
    if len(parts) == 3:
        return parts[1], parts[2]
    if "." in text:
        worker_name, repo_id = text.rsplit(".", 1)
        if worker_name and repo_id:
            return repo_id, worker_name
    return None
