"""Detect orphaned or stale implementing claims.

An implementer claim records which physical checkout (machine/repo/worker)
holds the issue in ``Issue.worker``. The worker registry refreshes each live
checkout's ``last_seen`` heartbeat (see ``WorkerHeartbeat``). Cross-referencing
the two surfaces claims whose holding worker is gone or has stopped
heartbeating, without any server-side lease: an issue stuck at
``stage=implementing`` with no live worker is an orphan that the pull pool will
never re-offer, so no idle agent picks it up either.

This module only detects and describes stale claims. Recovery (returning a
dead claim to the pool) is a separate change that needs a server-side
un-claim endpoint; see issuekit#168.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.store import get_store
from issuekit.worker_registry import list_api_workers

# The worker heartbeat posts every WORKER_HEARTBEAT_INTERVAL_SEC (60s). Wait for
# several missed beats before flagging so a healthy but briefly-delayed worker
# is not reported as stale.
DEFAULT_STALE_AFTER_SEC = 300.0

IMPLEMENTING_STAGE = "implementing"

# Reason codes for a flagged claim.
NO_WORKER = "no_worker"
EXPIRED_HEARTBEAT = "expired_heartbeat"


@dataclass(frozen=True)
class StaleClaim:
    """An implementing issue whose holding worker is gone or silent."""

    issue: Issue
    reason: str
    worker: str
    last_seen: str | None
    stale_seconds: float | None


def _worker_key(row: Mapping[str, object]) -> str:
    return "/".join(
        str(row.get(field, "")) for field in ("machine_id", "repo_id", "worker_id")
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def detect_stale_claims(
    issues: Iterable[Issue],
    workers: Iterable[Mapping[str, object]],
    *,
    now: datetime,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
) -> list[StaleClaim]:
    """Return implementing issues whose holding worker is gone or silent.

    An issue is reported when it is at ``stage=implementing``, records a worker,
    and either no live worker matches that key (``NO_WORKER``) or the matching
    worker's last heartbeat is older than ``stale_after_sec``
    (``EXPIRED_HEARTBEAT``). Issues without a recorded worker are skipped:
    there is no liveness signal to judge, so flagging them would be a guess.
    A worker whose ``last_seen`` is missing or unparseable is treated as live
    for the same reason.
    """
    last_seen_by_worker: dict[str, object] = {
        _worker_key(row): row.get("last_seen") for row in workers
    }
    stale: list[StaleClaim] = []
    for issue in issues:
        if issue.stage != IMPLEMENTING_STAGE:
            continue
        worker = issue.worker
        if not worker:
            continue
        if worker not in last_seen_by_worker:
            stale.append(StaleClaim(issue, NO_WORKER, worker, None, None))
            continue
        raw_last_seen = last_seen_by_worker[worker]
        seen = _parse_timestamp(raw_last_seen)
        if seen is None:
            continue
        age = (now - seen).total_seconds()
        if age > stale_after_sec:
            last_seen_str = raw_last_seen if isinstance(raw_last_seen, str) else None
            stale.append(
                StaleClaim(issue, EXPIRED_HEARTBEAT, worker, last_seen_str, age)
            )
    return stale


def list_stale_claims(
    config: IssuekitConfig,
    *,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
    now: datetime | None = None,
) -> list[StaleClaim]:
    """Load implementing issues and live workers, then detect stale claims.

    Workers are listed unfiltered: liveness is a property of the physical
    checkout (machine/repo/worker), independent of which project it registered
    under, so the full worker key is what identifies a live holder.
    """
    current = now or datetime.now(timezone.utc)
    workers = list_api_workers(config)
    with get_store(config) as store:
        issues = store.find_for(stage=IMPLEMENTING_STAGE)
    return detect_stale_claims(
        issues, workers, now=current, stale_after_sec=stale_after_sec
    )


def stale_claim_dict(claim: StaleClaim) -> dict[str, object]:
    """Serialize a stale claim for JSON output.

    Shared by the CLI and the MCP server so both paths emit identical payloads.
    """
    return {
        "id": claim.issue.id,
        "ref": claim.issue.ref,
        "title": claim.issue.title,
        "assignee": claim.issue.assignee,
        "worker": claim.worker,
        "reason": claim.reason,
        "last_seen": claim.last_seen,
        "stale_seconds": (
            None if claim.stale_seconds is None else int(claim.stale_seconds)
        ),
    }
