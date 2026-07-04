from datetime import datetime, timedelta, timezone

from issuekit.core import Issue
from issuekit.orphans import (
    DEFAULT_STALE_AFTER_SEC,
    EXPIRED_HEARTBEAT,
    NO_WORKER,
    detect_stale_claims,
    stale_claim_dict,
)


NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _issue(
    issue_id: int,
    *,
    stage: str = "implementing",
    worker: str = "machine/issuekit/checkout",
    assignee: str = "claude",
    title: str = "Task",
) -> Issue:
    return Issue(
        id=issue_id,
        ref=f"issuekit#{issue_id}",
        title=title,
        issue_status="in_progress",
        created="2026-07-01",
        completed="",
        priority="medium",
        assignee=assignee,
        stage=stage,
        implementer=assignee,
        author="repom",
        body="",
        metadata={},
        worker=worker,
    )


def _worker(worker: str, last_seen: str | None) -> dict[str, object]:
    machine, repo, wid = worker.split("/")
    return {
        "machine_id": machine,
        "repo_id": repo,
        "worker_id": wid,
        "last_seen": last_seen,
    }


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_no_live_worker_is_orphaned() -> None:
    issue = _issue(1, worker="machine/issuekit/dead")

    claims = detect_stale_claims([issue], [], now=NOW)

    assert len(claims) == 1
    assert claims[0].reason == NO_WORKER
    assert claims[0].worker == "machine/issuekit/dead"
    assert claims[0].last_seen is None
    assert claims[0].stale_seconds is None


def test_recent_heartbeat_is_healthy() -> None:
    worker = "machine/issuekit/live"
    workers = [_worker(worker, _iso(NOW - timedelta(seconds=30)))]

    claims = detect_stale_claims([_issue(1, worker=worker)], workers, now=NOW)

    assert claims == []


def test_expired_heartbeat_is_stale() -> None:
    worker = "machine/issuekit/slow"
    last_seen = _iso(NOW - timedelta(seconds=DEFAULT_STALE_AFTER_SEC + 60))
    workers = [_worker(worker, last_seen)]

    claims = detect_stale_claims([_issue(1, worker=worker)], workers, now=NOW)

    assert len(claims) == 1
    assert claims[0].reason == EXPIRED_HEARTBEAT
    assert claims[0].last_seen == last_seen
    assert claims[0].stale_seconds is not None
    assert claims[0].stale_seconds > DEFAULT_STALE_AFTER_SEC


def test_age_exactly_at_threshold_is_not_stale() -> None:
    worker = "machine/issuekit/edge"
    workers = [_worker(worker, _iso(NOW - timedelta(seconds=DEFAULT_STALE_AFTER_SEC)))]

    claims = detect_stale_claims([_issue(1, worker=worker)], workers, now=NOW)

    assert claims == []


def test_issue_without_recorded_worker_is_skipped() -> None:
    claims = detect_stale_claims([_issue(1, worker="")], [], now=NOW)

    assert claims == []


def test_non_implementing_stage_is_skipped() -> None:
    issue = _issue(1, stage="review", worker="machine/issuekit/dead")

    claims = detect_stale_claims([issue], [], now=NOW)

    assert claims == []


def test_unparseable_last_seen_is_treated_as_live() -> None:
    worker = "machine/issuekit/weird"
    workers = [_worker(worker, "not-a-timestamp")]

    claims = detect_stale_claims([_issue(1, worker=worker)], workers, now=NOW)

    assert claims == []


def test_missing_last_seen_is_treated_as_live() -> None:
    worker = "machine/issuekit/nobeat"
    workers = [_worker(worker, None)]

    claims = detect_stale_claims([_issue(1, worker=worker)], workers, now=NOW)

    assert claims == []


def test_naive_last_seen_is_assumed_utc() -> None:
    worker = "machine/issuekit/naive"
    # No timezone suffix: parsed as UTC, well within the window -> healthy.
    workers = [_worker(worker, "2026-07-04T11:59:00")]

    claims = detect_stale_claims([_issue(1, worker=worker)], workers, now=NOW)

    assert claims == []


def test_stale_claim_dict_shape() -> None:
    worker = "machine/issuekit/slow"
    last_seen = _iso(NOW - timedelta(seconds=DEFAULT_STALE_AFTER_SEC + 60))
    workers = [_worker(worker, last_seen)]

    claims = detect_stale_claims([_issue(7, worker=worker)], workers, now=NOW)
    payload = stale_claim_dict(claims[0])

    assert payload == {
        "id": 7,
        "ref": "issuekit#7",
        "title": "Task",
        "assignee": "claude",
        "worker": worker,
        "reason": EXPIRED_HEARTBEAT,
        "last_seen": last_seen,
        "stale_seconds": int(claims[0].stale_seconds),
    }
