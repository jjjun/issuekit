"""Worker address validation for directed issues."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from issuekit.config import IssuekitConfig
from issuekit.core import worker_keys_from_row, worker_keys_match
from issuekit.workflow import WorkflowError


def validate_target_worker(
    address: str,
    *,
    config: IssuekitConfig,
    workers: Sequence[Mapping[str, object]],
    allow_unregistered: bool = False,
) -> str:
    target = address.strip()
    if not target:
        raise WorkflowError("Target worker address is required.", code="invalid_worker")
    if allow_unregistered:
        return target
    keys = (
        key
        for worker in workers
        for key in worker_keys_from_row(worker)
        if "@" not in target or "@" in key
    )
    if any(worker_keys_match(target, key) for key in keys):
        return target
    raise WorkflowError(
        f"Target worker is not registered for project {config.project}: {target}. "
        "Rerun with --allow-unregistered-worker only when directing work to a "
        "worker that has not registered yet.",
        code="worker_not_found",
    )


def target_worker_repo_id(address: str) -> str | None:
    identity = address.strip().partition("@")[0]
    if "." not in identity:
        return None
    return identity.rsplit(".", 1)[1] or None
