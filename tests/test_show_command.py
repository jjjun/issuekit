import json
from datetime import datetime, timedelta
from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.agentrun.status import STALE_AFTER_SEC, RunStatus, status_path, write_status
from issuekit.testing import FakeIssuekitClient
from tests.issue_helpers import api_issue


def _configure_api(tmp_path: Path, monkeypatch, client: FakeIssuekitClient) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def _write_run(run_dir: Path, run_id: str, *, issue: int, heartbeat_at: str) -> None:
    write_status(
        status_path(run_dir, run_id),
        RunStatus(
            run_id=run_id,
            agent="codex",
            issue=issue,
            status="running",
            pid=123,
            started_at=heartbeat_at,
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=f".agent-runs/{run_id}.out.log",
            agent_log=f".agent-runs/{run_id}.agent.log",
            heartbeat_at=heartbeat_at,
        ),
    )


def test_show_flags_stale_run_when_issue_stuck_implementing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    _configure_api(tmp_path, monkeypatch, client)
    old_heartbeat = (
        datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)
    ).replace(microsecond=0).isoformat()
    _write_run(tmp_path / ".agent-runs", "stale-run", issue=1, heartbeat_at=old_heartbeat)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" in payload
    assert "stale-run" in payload["stale_run"]


def test_show_flags_already_reconciled_abandoned_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    _configure_api(tmp_path, monkeypatch, client)
    old_heartbeat = (
        datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)
    ).replace(microsecond=0).isoformat()
    write_status(
        status_path(tmp_path / ".agent-runs", "stale-run"),
        RunStatus(
            run_id="stale-run",
            agent="codex",
            issue=1,
            status="abandoned",
            pid=123,
            started_at=old_heartbeat,
            ended_at=old_heartbeat,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/stale-run.out.log",
            agent_log=".agent-runs/stale-run.agent.log",
            heartbeat_at=old_heartbeat,
            terminal_reason="heartbeat_lost",
        ),
    )

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" in payload
    assert "stale-run" in payload["stale_run"]


def test_show_does_not_flag_fresh_run(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    _configure_api(tmp_path, monkeypatch, client)
    fresh_heartbeat = datetime.now().replace(microsecond=0).isoformat()
    _write_run(tmp_path / ".agent-runs", "live-run", issue=1, heartbeat_at=fresh_heartbeat)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" not in payload


def test_show_ignores_stale_run_when_issue_not_implementing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="review", assignee="claude")]
    )
    _configure_api(tmp_path, monkeypatch, client)
    old_heartbeat = (
        datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)
    ).replace(microsecond=0).isoformat()
    _write_run(tmp_path / ".agent-runs", "stale-run", issue=1, heartbeat_at=old_heartbeat)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" not in payload


def test_show_is_best_effort_without_agent_runs_directory(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" not in payload
