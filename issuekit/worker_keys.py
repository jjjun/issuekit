"""Worker identity key helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


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


def worker_keys_match(left: str, right: str) -> bool:
    """Return True when two worker keys refer to the same worker identity.

    Machine ids discriminate only when both keys carry the machine-qualified
    ``@machine`` suffix. Bare ``worker.repo`` keys stay machine-agnostic.
    """
    if left == right:
        return True
    left_parts = _key_parts(left)
    right_parts = _key_parts(right)
    if left_parts is None or right_parts is None:
        return False
    return _parts_match(left_parts, right_parts, require_target_machine=False)


def directed_target_matches(target_worker: str, claiming_key: str) -> bool:
    """Return True when a claiming worker key satisfies a directed target.

    Mirrors the API server semantics: a machine-qualified target only matches
    a claiming key that carries the same machine id, while a bare target stays
    machine-agnostic.
    """
    if target_worker == claiming_key:
        return True
    target_parts = _key_parts(target_worker)
    claiming_parts = _key_parts(claiming_key)
    if target_parts is None or claiming_parts is None:
        return False
    return _parts_match(target_parts, claiming_parts, require_target_machine=True)


@dataclass(frozen=True)
class _KeyParts:
    worker_name: str
    repo_id: str | None
    machine_id: str | None


def _parts_match(
    target: _KeyParts,
    other: _KeyParts,
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
    return _string(row.get("worker_name") or row.get("worker_id"))


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _key_parts(value: str) -> _KeyParts | None:
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
        return _KeyParts(
            worker_name=worker_name,
            repo_id=repo_id,
            machine_id=machine_id or None,
        )
    if machine_id:
        return _KeyParts(worker_name=left, repo_id=None, machine_id=machine_id)
    # A bare worker name carries no repo or machine context; only exact string
    # equality identifies it, matching the pre-qualified behavior.
    return None
