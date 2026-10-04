import time
from pathlib import Path

import issuekit.agentrun.logtail as logtail_module
import issuekit.agentrun.watcher as watcher_module
from issuekit.agentrun.status import RunStatus, read_status, status_path, write_status
from issuekit.agentrun.watcher import _RunWatcher


def _running_status() -> RunStatus:
    return RunStatus(
        run_id="run-a",
        agent="codex",
        issue=307,
        status="running",
        pid=123,
        started_at="2026-07-26T12:00:00",
        ended_at=None,
        elapsed_sec=None,
        exit_code=None,
        plan=".agent-runs/issue-307.md",
        stdout_log=".agent-runs/run-a.out.log",
        agent_log=".agent-runs/run-a.agent.log",
    )


def test_watcher_reads_only_the_tail_window_from_large_logs(
    tmp_path: Path, monkeypatch
) -> None:
    run_status = _running_status()
    run_status_path = status_path(tmp_path, run_status.run_id)
    stdout_log_path = tmp_path / "stdout.log"
    agent_log_path = tmp_path / "agent.log"
    stdout_log_path.write_bytes((b"x" * 99 + b"\n") * 52_000)
    agent_log_path.write_text("small log\n", encoding="utf-8", newline="\n")
    write_status(run_status_path, run_status)
    read_sizes: dict[str, int] = {}
    read_tail = logtail_module.read_tail

    def measured_read_tail(path: Path, *, max_bytes: int = 65_536) -> bytes:
        data = read_tail(path, max_bytes=max_bytes)
        read_sizes[path.name] = len(data)
        return data

    monkeypatch.setattr(logtail_module, "read_tail", measured_read_tail)
    watcher = _RunWatcher(
        run_status_path=run_status_path,
        run_status=run_status,
        repo=tmp_path,
        agent_log_path=agent_log_path,
        stdout_log_path=stdout_log_path,
        enable_heartbeat=False,
        start_time=time.monotonic(),
    )

    watcher._tick()

    assert stdout_log_path.stat().st_size > 5_000_000
    assert read_sizes["stdout.log"] <= 65_536
    assert read_sizes["agent.log"] <= 65_536


def test_watcher_throttles_changed_file_count(tmp_path: Path, monkeypatch) -> None:
    run_status = _running_status()
    run_status_path = status_path(tmp_path, run_status.run_id)
    agent_log_path = tmp_path / "agent.log"
    write_status(run_status_path, run_status)
    now = {"value": 0.0}
    calls: list[Path] = []
    monkeypatch.setattr(watcher_module.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        watcher_module,
        "changed_file_count",
        lambda repo: calls.append(repo) or len(calls),
    )
    watcher = _RunWatcher(
        run_status_path=run_status_path,
        run_status=run_status,
        repo=tmp_path,
        agent_log_path=agent_log_path,
        enable_heartbeat=True,
        start_time=0.0,
    )

    watcher._tick()
    now["value"] = 1.0
    watcher._tick()
    now["value"] = 4.999
    watcher._tick()
    assert len(calls) == 1
    now["value"] = watcher_module.HEARTBEAT_GIT_INTERVAL_SEC
    watcher._tick()

    assert len(calls) == 2
    assert read_status(run_status_path).heartbeat_at is not None
