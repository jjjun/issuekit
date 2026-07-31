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
    if any(_worker_row_matches(worker, target) for worker in workers):
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


def resolve_registered_worker_address(
    workers: Sequence[Mapping[str, object]],
    *,
    project: str,
    address: str | None = None,
) -> str:
    candidate_workers = tuple(
        sorted(
            (
                (candidate, worker)
                for worker in workers
                if (candidate := _preferred_worker_address(worker)) is not None
            ),
            key=lambda item: item[0],
        )
    )
    candidates = tuple(candidate for candidate, _worker in candidate_workers)
    if address is None:
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise WorkflowError(
                f"No registered workers found for project {project}.",
                code="worker_not_found",
            )
        raise WorkflowError(
            f"Multiple registered workers found for project {project}. "
            "Pass --worker with one of: "
            f"{', '.join(_worker_candidate_description(candidate, worker) for candidate, worker in candidate_workers)}.",
            code="worker_ambiguous",
        )

    target = address.strip()
    if not target:
        raise WorkflowError("Worker address is required.", code="invalid_worker")
    matches = [
        worker
        for worker in workers
        if _worker_row_matches(worker, target)
    ]
    if not matches:
        suffix = (
            f" Choose one of: {', '.join(candidates)}."
            if candidates
            else ""
        )
        raise WorkflowError(
            f"Worker is not registered for project {project}: {target}.{suffix}",
            code="worker_not_found",
        )
    if len(matches) > 1:
        matching_candidates = tuple(
            sorted(_preferred_worker_address(worker) for worker in matches)
        )
        raise WorkflowError(
            f"Worker address is ambiguous for project {project}: {target}. "
            f"Choose one of: {', '.join(matching_candidates)}.",
            code="worker_ambiguous",
        )
    return _worker_address(matches[0], qualified="@" in target)


def registered_worker_row(
    workers: Sequence[Mapping[str, object]],
    address: str,
) -> Mapping[str, object] | None:
    """Return the unique registered worker row matching a resolved address."""

    matches = [worker for worker in workers if _worker_row_matches(worker, address)]
    return matches[0] if len(matches) == 1 else None


def _worker_row_matches(worker: Mapping[str, object], target: str) -> bool:
    return any(
        worker_keys_match(target, key)
        for key in worker_keys_from_row(worker)
        if "@" not in target or "@" in key
    )


def _preferred_worker_address(worker: Mapping[str, object]) -> str | None:
    keys = worker_keys_from_row(worker)
    qualified = sorted(key for key in keys if "@" in key)
    if qualified:
        return qualified[0]
    bare = sorted(keys)
    if bare:
        return bare[0]
    return None


def _worker_address(worker: Mapping[str, object], *, qualified: bool) -> str:
    keys = worker_keys_from_row(worker)
    matching = sorted(key for key in keys if ("@" in key) == qualified)
    if matching:
        return matching[0]
    preferred = _preferred_worker_address(worker)
    if preferred is not None:
        return preferred
    raise WorkflowError(
        "Registered worker response did not contain a usable worker address.",
        code="invalid_response",
    )


def _worker_candidate_description(
    address: str,
    worker: Mapping[str, object],
) -> str:
    status = str(worker.get("status") or "unknown")
    last_seen = str(worker.get("last_seen") or "unknown")
    return f"{address} (status={status}, last_seen={last_seen})"
