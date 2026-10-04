"""Create checks for pending proposals."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from issuekit.config import IssuekitConfig
from issuekit.proposals.api import api_client
from issuekit.timestamps import parse_timestamp, utcnow
from issuekit.workers.addressing import (
    registered_worker_row,
    resolve_registered_worker_address,
)

PROPOSAL_CHECK_WORKER_STALE_AFTER_SEC = 300.0


def request_proposal_check(
    config: IssuekitConfig,
    *,
    to: str,
    proposal_id: int,
    worker: str | None = None,
) -> dict:
    with api_client(config, project=to) as client:
        workers = client.list_workers(project=to)
        target_worker = resolve_registered_worker_address(
            workers,
            project=to,
            address=worker,
        )
        selected_worker = registered_worker_row(workers, target_worker)
        check = client.create_proposal_check(
            proposal_id,
            target_worker=target_worker,
            project=to,
        )
    result = dict(check)
    result["worker_auto_selected"] = worker is None
    warning = _worker_liveness_warning(target_worker, selected_worker)
    if warning is not None:
        result["warnings"] = [warning]
    return result


def _worker_liveness_warning(
    target_worker: str,
    worker: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> str | None:
    if worker is None:
        return None
    status = str(worker.get("status") or "unknown")
    last_seen = str(worker.get("last_seen") or "unknown")
    seen = parse_timestamp(worker.get("last_seen"))
    age_seconds = (
        None
        if seen is None
        else max(0, int(((now or utcnow()) - seen).total_seconds()))
    )
    if status != "offline" and (
        age_seconds is None or age_seconds <= PROPOSAL_CHECK_WORKER_STALE_AFTER_SEC
    ):
        return None
    age = "unknown" if age_seconds is None else f"{age_seconds}s"
    return (
        f"Target worker {target_worker} may be unreachable: "
        f"status={status}, last_seen={last_seen}, age={age}."
    )
