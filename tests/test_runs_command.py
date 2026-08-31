import io
import json
from datetime import datetime
from pathlib import Path

from issuekit import cli
from issuekit.agentrun.status import RunStatus, status_path, write_status


def test_runs_lists_newest_first(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    _write_run(
        run_dir,
        "20260608-111000",
        status="completed",
        agent="codex",
        issue=39,
        started_at="2026-06-08T11:10:00",
        elapsed_sec=2.5,
        exit_code=0,
    )
    _write_run(
        run_dir,
        "20260608-111200",
        status="running",
        agent="kimi",
        issue=41,
        started_at="2026-06-08T11:12:00",
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("RUN ID")
    assert "20260608-111200" in lines[1]
    assert "kimi" in lines[1]
    assert "running" in lines[1]
    assert "20260608-111000" in lines[2]
    assert "completed" in lines[2]
    assert "2.50s" in lines[2]


def test_runs_active_filters_to_running(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    _write_run(run_dir, "done", status="completed", exit_code=0, elapsed_sec=1.0)
    _write_run(run_dir, "live", status="running")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "--active"]) == 0

    output = capsys.readouterr().out
    assert "live" in output
    assert "done" not in output


def test_runs_json_outputs_records(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    _write_run(run_dir, "run-a", status="completed", issue=None, exit_code=0)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "--json"]) == 0

    records = json.loads(capsys.readouterr().out)
    assert records[0]["run_id"] == "run-a"
    assert records[0]["issue"] is None


def test_runs_skips_unreadable_records_and_warns(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    run_dir = tmp_path / ".agent-runs"
    _write_run(run_dir, "good", status="completed", exit_code=0)
    (run_dir / "nul.status.json").write_bytes(b"\0" * 470)
    (run_dir / "missing.status.json").write_text(
        '{"run_id": "missing"}\n', encoding="utf-8", newline="\n"
    )
    (run_dir / "array.status.json").write_text(
        "[]\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs"]) == 0

    captured = capsys.readouterr()
    assert "good" in captured.out
    assert "nul.status.json" in captured.err
    assert "missing.status.json" in captured.err
    assert "array.status.json" in captured.err


def test_runs_json_skips_unreadable_records_and_stays_parseable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    run_dir = tmp_path / ".agent-runs"
    _write_run(run_dir, "good", status="completed", exit_code=0)
    (run_dir / "bad.status.json").write_bytes(b"\0")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "--json"]) == 0

    captured = capsys.readouterr()
    records = json.loads(captured.out)
    assert [record["run_id"] for record in records] == ["good"]
    assert "bad.status.json" in captured.err


def test_runs_detail_prints_record_and_log_tails(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    stdout_log = run_dir / "detail.out.log"
    agent_log = run_dir / "detail.agent.log"
    stdout_log.write_text(
        "\n".join(f"out-{index}" for index in range(45)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    agent_log.write_text("err-one\nerr-two\n", encoding="utf-8", newline="\n")
    _write_run(
        run_dir,
        "detail",
        status="failed",
        stdout_log=".agent-runs/detail.out.log",
        agent_log=".agent-runs/detail.agent.log",
        exit_code=1,
        elapsed_sec=3.0,
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "detail"]) == 0

    output = capsys.readouterr().out
    output_lines = output.splitlines()
    assert '"run_id": "detail"' in output
    assert "--- stdout tail" in output
    assert "out-5" in output
    assert "out-44" in output
    assert "out-4" not in output_lines
    assert "err-two" in output


def test_runs_detail_missing_returns_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "missing"]) == 1

    assert "Run not found: missing" in capsys.readouterr().err


def test_runs_detail_unreadable_returns_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    path = run_dir / "broken.status.json"
    path.write_bytes(b"\0")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "broken"]) == 1

    error = capsys.readouterr().err
    assert "Run status file is unreadable" in error
    assert str(path) in error
    assert "Run not found" not in error


def test_runs_detail_reads_agent_log(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    agent_log = run_dir / "legacy.agent.log"
    agent_log.write_text("legacy-agent-line\n", encoding="utf-8", newline="\n")
    status_json = {
        "run_id": "legacy",
        "agent": "codex",
        "issue": 1,
        "status": "completed",
        "pid": None,
        "started_at": "2026-06-08T11:00:00",
        "ended_at": "2026-06-08T11:00:01",
        "elapsed_sec": 1.0,
        "exit_code": 0,
        "plan": "docs/issues/active/001_first.md",
        "stdout_log": ".agent-runs/legacy.out.log",
        "agent_log": ".agent-runs/legacy.agent.log",
    }
    (run_dir / "legacy.status.json").write_text(
        json.dumps(status_json, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "legacy"]) == 0

    output = capsys.readouterr().out
    assert '"run_id": "legacy"' in output
    assert "legacy-agent-line" in output


def _write_run(
    run_dir: Path,
    run_id: str,
    *,
    status: str,
    agent: str = "codex",
    issue: int | None = 1,
    started_at: str = "2026-06-08T11:00:00",
    elapsed_sec: float | None = None,
    exit_code: int | None = None,
    stdout_log: str | None = None,
    agent_log: str | None = None,
) -> None:
    # A live running record has a fresh heartbeat; only deliberately old ones are stale.
    heartbeat_at = (
        datetime.now().replace(microsecond=0).isoformat()
        if status == "running"
        else None
    )
    write_status(
        status_path(run_dir, run_id),
        RunStatus(
            run_id=run_id,
            agent=agent,
            issue=issue,
            status=status,
            pid=123 if status == "running" else None,
            started_at=started_at,
            ended_at=None if status == "running" else "2026-06-08T11:00:01",
            elapsed_sec=elapsed_sec,
            exit_code=exit_code,
            plan="docs/issues/active/001_first.md",
            stdout_log=stdout_log or f".agent-runs/{run_id}.out.log",
            agent_log=agent_log or f".agent-runs/{run_id}.agent.log",
            heartbeat_at=heartbeat_at,
        ),
    )


def test_runs_reconciles_stale_running_to_abandoned_everywhere(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from datetime import datetime, timedelta

    from issuekit.agentrun.status import STALE_AFTER_SEC

    run_dir = tmp_path / ".agent-runs"
    old = (datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)).replace(
        microsecond=0
    ).isoformat()
    write_status(
        status_path(run_dir, "frozen"),
        RunStatus(
            run_id="frozen",
            agent="codex",
            issue=61,
            status="running",
            pid=123,
            started_at=old,
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/061_x.md",
            stdout_log=".agent-runs/frozen.out.log",
            agent_log=".agent-runs/frozen.agent.log",
            heartbeat_at=old,
        ),
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs"]) == 0
    table = capsys.readouterr().out
    assert "abandoned" in table

    assert cli.main(["runs", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert records[0]["status"] == "abandoned"
    assert records[0]["terminal_reason"] == "heartbeat_lost"
    assert records[0]["ended_at"] == old

    assert cli.main(["runs", "frozen", "--json"]) == 0
    detail = json.loads(capsys.readouterr().out)
    assert detail["status"] == "abandoned"
    assert detail["terminal_reason"] == "heartbeat_lost"

    on_disk = json.loads(status_path(run_dir, "frozen").read_text(encoding="utf-8"))
    assert on_disk["status"] == "abandoned"


def test_runs_leaves_fresh_running_record_untouched(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    run_dir = tmp_path / ".agent-runs"
    fresh_heartbeat = datetime.now().replace(microsecond=0).isoformat()
    write_status(
        status_path(run_dir, "live"),
        RunStatus(
            run_id="live",
            agent="codex",
            issue=61,
            status="running",
            pid=123,
            started_at=fresh_heartbeat,
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/061_x.md",
            stdout_log=".agent-runs/live.out.log",
            agent_log=".agent-runs/live.agent.log",
            heartbeat_at=fresh_heartbeat,
        ),
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert records[0]["status"] == "running"

    on_disk = json.loads(status_path(run_dir, "live").read_text(encoding="utf-8"))
    assert on_disk["status"] == "running"


def test_runs_list_shows_last_log_line(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    write_status(
        status_path(run_dir, "run-a"),
        RunStatus(
            run_id="run-a",
            agent="kimi",
            issue=1,
            status="running",
            pid=123,
            started_at="2026-06-08T11:00:00",
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/run-a.out.log",
            agent_log=".agent-runs/run-a.agent.log",
            last_log_line=" agent is processing...",
            last_log_at="2026-06-08T11:00:05",
            heartbeat_at="2026-06-08T11:00:05",
        ),
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs"]) == 0

    output = capsys.readouterr().out
    assert "agent is processing..." in output
    assert "LAST LOG" in output


def test_runs_output_escapes_characters_unsupported_by_console_encoding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / ".agent-runs"
    write_status(
        status_path(run_dir, "run-a"),
        RunStatus(
            run_id="run-a",
            agent="codex",
            issue=1,
            status="running",
            pid=123,
            started_at="2026-06-08T11:00:00",
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/run-a.out.log",
            agent_log=".agent-runs/run-a.agent.log",
            last_log_line="\u8f7d log",
            last_log_at="2026-06-08T11:00:05",
            heartbeat_at="2026-06-08T11:00:05",
        ),
    )
    monkeypatch.chdir(tmp_path)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp932", errors="strict")
    monkeypatch.setattr("sys.stdout", stream)

    assert cli.main(["runs"]) == 0

    stream.flush()
    output = buffer.getvalue().decode("cp932")
    assert "\\u8f7d log" in output
