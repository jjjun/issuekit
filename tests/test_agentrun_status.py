import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from issuekit.agentrun import status as status_mod
from issuekit.agentrun.status import (
    STALE_AFTER_SEC,
    RunStatus,
    is_stale,
    read_status,
    status_path,
    write_status,
)


def _running_status(run_id: str = "run-a", **overrides) -> RunStatus:
    base = dict(
        run_id=run_id,
        agent="codex",
        issue=1,
        status="running",
        pid=123,
        started_at="2026-06-17T12:00:00",
        ended_at=None,
        elapsed_sec=None,
        exit_code=None,
        plan="docs/issues/active/001_first.md",
        stdout_log=f".agent-runs/{run_id}.out.log",
        agent_log=f".agent-runs/{run_id}.agent.log",
        heartbeat_at="2026-06-17T12:00:00",
    )
    base.update(overrides)
    return RunStatus(**base)


def test_write_status_retries_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    path = status_path(tmp_path / ".agent-runs", "run-a")
    calls = {"n": 0}
    real_replace = Path.replace

    def flaky_replace(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("WinError 5: Access is denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(status_mod.time, "sleep", lambda _s: None)

    write_status(path, _running_status())

    assert calls["n"] == 3
    assert read_status(path).run_id == "run-a"


def test_write_status_falls_back_to_in_place_write(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / ".agent-runs"
    path = status_path(run_dir, "run-a")

    def always_fail(self, target):
        raise PermissionError("WinError 32: sharing violation")

    monkeypatch.setattr(Path, "replace", always_fail)
    monkeypatch.setattr(status_mod.time, "sleep", lambda _s: None)

    write_status(path, _running_status())

    # Destination still ends up with correct content and the temp file is gone.
    assert read_status(path).run_id == "run-a"
    leftovers = list(run_dir.glob(".*tmp"))
    assert leftovers == []


def test_write_status_does_not_raise_on_persistent_failure(tmp_path: Path, monkeypatch) -> None:
    path = status_path(tmp_path / ".agent-runs", "run-a")

    def always_fail(self, target):
        raise PermissionError("WinError 5")

    monkeypatch.setattr(Path, "replace", always_fail)
    monkeypatch.setattr(status_mod.time, "sleep", lambda _s: None)

    # Must not raise even though the atomic swap can never succeed.
    write_status(path, _running_status())
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"


def test_write_status_does_not_raise_when_fallback_write_also_fails(
    tmp_path: Path, monkeypatch
) -> None:
    # The genuine lock-contention case: the destination is held, so both the atomic
    # replace AND the in-place fallback write fail. write_status must still not raise,
    # otherwise the main-thread terminal-status write in run() would crash before
    # AgentResult is returned (the exact issue #61 symptom).
    path = status_path(tmp_path / ".agent-runs", "run-a")

    def always_fail_replace(self, target):
        raise PermissionError("WinError 5: Access is denied")

    real_write_text = Path.write_text

    def fail_dest_write_text(self, *args, **kwargs):
        if self == path:
            raise PermissionError("WinError 5: Access is denied")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", always_fail_replace)
    monkeypatch.setattr(Path, "write_text", fail_dest_write_text)
    monkeypatch.setattr(status_mod.time, "sleep", lambda _s: None)

    # No exception, and no stray temp file is left behind.
    write_status(path, _running_status())
    assert list((tmp_path / ".agent-runs").glob(".*tmp")) == []


def test_is_stale_true_for_old_heartbeat() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0)
    old = (now - timedelta(seconds=STALE_AFTER_SEC + 10)).isoformat()
    assert is_stale(_running_status(heartbeat_at=old), now=now) is True


def test_is_stale_false_for_fresh_heartbeat() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0)
    fresh = (now - timedelta(seconds=1)).isoformat()
    assert is_stale(_running_status(heartbeat_at=fresh), now=now) is False


def test_is_stale_false_for_non_running_status() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0)
    old = (now - timedelta(seconds=STALE_AFTER_SEC + 10)).isoformat()
    completed = _running_status(status="completed", heartbeat_at=old)
    assert is_stale(completed, now=now) is False


def test_is_stale_falls_back_to_started_at_when_no_heartbeat() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0)
    old = (now - timedelta(seconds=STALE_AFTER_SEC + 10)).isoformat()
    assert is_stale(_running_status(started_at=old, heartbeat_at=None), now=now) is True


def test_is_stale_false_for_unparseable_timestamp() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0)
    assert is_stale(_running_status(heartbeat_at="not-a-date"), now=now) is False


def test_is_stale_false_for_tz_aware_timestamp_against_naive_now() -> None:
    # A tz-aware stamp subtracted from naive now() raises TypeError; must not crash.
    now = datetime(2026, 6, 17, 12, 0, 0)
    aware = "2026-06-17T11:00:00+00:00"
    assert is_stale(_running_status(heartbeat_at=aware), now=now) is False
