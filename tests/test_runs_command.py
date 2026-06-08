import json
from pathlib import Path

from issuekit import cli
from issuekit.agents.status import RunStatus, status_path, write_status


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


def test_runs_detail_reads_legacy_err_log(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    stderr_log = run_dir / "legacy.err.log"
    stderr_log.write_text("legacy-err-line\n", encoding="utf-8", newline="\n")
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
        "stderr_log": ".agent-runs/legacy.err.log",
    }
    (run_dir / "legacy.status.json").write_text(
        json.dumps(status_json, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", "legacy"]) == 0

    output = capsys.readouterr().out
    assert '"run_id": "legacy"' in output
    assert "legacy-err-line" in output


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
        ),
    )


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
