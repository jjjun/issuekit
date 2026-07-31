"""Detect orphaned or stale implementing claims.

An implementer claim records which worker checkout (worker.repo) holds the
issue in ``Issue.worker``. The worker registry refreshes each live
checkout's ``last_seen`` heartbeat (see ``WorkerHeartbeat``). Cross-referencing
the two surfaces claims whose holding worker is gone or has stopped
heartbeating, without any server-side lease: an issue stuck at
``stage=implementing`` with no live worker is an orphan that the pull pool will
never re-offer, so no idle agent picks it up either.

Directed issues can also stall when their target worker disappears before
claiming them. These are reported alongside stale claims so an operator can
return them to the repo pool with ``issuekit readdress``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from issuekit.config import IssuekitConfig
from issuekit.core import Issue, worker_keys_from_row, worker_keys_match
from issuekit.timestamps import parse_timestamp

# The worker heartbeat posts at the configured worker_heartbeat_interval_sec,
# which defaults to 60s. Wait for several missed beats before flagging so a
# healthy but briefly-delayed worker is not reported as stale.
DEFAULT_STALE_AFTER_SEC = 300.0

IMPLEMENTING_STAGE = "implementing"

# Reason codes for a flagged claim.
NO_WORKER = "no_worker"
EXPIRED_HEARTBEAT = "expired_heartbeat"
DIRECTED_NO_WORKER = "directed_no_worker"
DIRECTED_EXPIRED_HEARTBEAT = "directed_expired_heartbeat"
READY_DIRECTED_STAGES = {"", "todo", "changes_requested"}


@dataclass(frozen=True)
class StaleClaim:
    """An implementing issue whose holding worker is gone or silent."""

    issue: Issue
    reason: str
    worker: str
    last_seen: str | None
    stale_seconds: float | None
    target_worker: str = ""


def detect_stale_claims(
    issues: Iterable[Issue],
    workers: Iterable[Mapping[str, object]],
    *,
    now: datetime,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
) -> list[StaleClaim]:
    """Return issues whose worker-directed state is gone or silent.

    An issue is reported when it is at ``stage=implementing``, records a worker,
    and either no live worker matches that key (``NO_WORKER``) or the matching
    worker's last heartbeat is older than ``stale_after_sec``
    (``EXPIRED_HEARTBEAT``). Issues without a recorded worker are skipped:
    there is no liveness signal to judge, so flagging them would be a guess.
    A worker whose ``last_seen`` is missing or unparseable is treated as live
    for the same reason.

    Ready issues with ``target_worker`` set are reported with directed reason
    codes when their target worker is missing or stale, because no other worker
    can claim them until they are readdressed to the repo pool.
    """
    last_seen_by_worker: dict[str, object] = {}
    for row in workers:
        for key in worker_keys_from_row(row):
            last_seen_by_worker[key] = row.get("last_seen")
    stale: list[StaleClaim] = []
    for issue in issues:
        if issue.stage != IMPLEMENTING_STAGE:
            if issue.stage in READY_DIRECTED_STAGES and issue.target_worker:
                _append_stale_worker(
                    stale,
                    issue,
                    worker=issue.target_worker,
                    last_seen_by_worker=last_seen_by_worker,
                    now=now,
                    stale_after_sec=stale_after_sec,
                    no_worker_reason=DIRECTED_NO_WORKER,
                    expired_reason=DIRECTED_EXPIRED_HEARTBEAT,
                    target_worker=issue.target_worker,
                )
            continue
        if issue.worker:
            _append_stale_worker(
                stale,
                issue,
                worker=issue.worker,
                last_seen_by_worker=last_seen_by_worker,
                now=now,
                stale_after_sec=stale_after_sec,
                no_worker_reason=NO_WORKER,
                expired_reason=EXPIRED_HEARTBEAT,
            )
    return stale


def list_stale_claims(
    config: IssuekitConfig,
    *,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
    now: datetime | None = None,
) -> list[StaleClaim]:
    """Load implementing issues and live workers, then detect stale claims.

    Workers are listed unfiltered: liveness is a property of the worker
    checkout, independent of which project it registered under, so the worker
    key is what identifies a live holder.
    """
    from issuekit.store import get_store
    from issuekit.workers.registry import list_api_workers

    current = now or datetime.now(UTC)
    workers = list_api_workers(config)
    with get_store(config) as store:
        issues = store.find_for()
    return detect_stale_claims(
        issues, workers, now=current, stale_after_sec=stale_after_sec
    )


def stale_claim_dict(claim: StaleClaim) -> dict[str, object]:
    """Serialize a stale claim for JSON output.

    Shared by the CLI and the MCP server so both paths emit identical payloads.
    """
    payload: dict[str, object] = {
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
    if claim.target_worker:
        payload["target_worker"] = claim.target_worker
    return payload


def _append_stale_worker(
    stale: list[StaleClaim],
    issue: Issue,
    *,
    worker: str,
    last_seen_by_worker: Mapping[str, object],
    now: datetime,
    stale_after_sec: float,
    no_worker_reason: str,
    expired_reason: str,
    target_worker: str = "",
) -> None:
    matched_key = worker if worker in last_seen_by_worker else None
    if matched_key is None:
        matched_key = next(
            (key for key in last_seen_by_worker if worker_keys_match(worker, key)),
            None,
        )
    if matched_key is None:
        stale.append(StaleClaim(issue, no_worker_reason, worker, None, None, target_worker))
        return
    raw_last_seen = last_seen_by_worker[matched_key]
    seen = parse_timestamp(raw_last_seen)
    if seen is None:
        return
    age = (now - seen).total_seconds()
    if age <= stale_after_sec:
        return
    last_seen_str = raw_last_seen if isinstance(raw_last_seen, str) else None
    stale.append(
        StaleClaim(issue, expired_reason, worker, last_seen_str, age, target_worker)
    )
