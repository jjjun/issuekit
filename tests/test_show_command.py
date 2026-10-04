import json
from datetime import datetime, timedelta
from pathlib import Path

from issuekit import cli
from issuekit.agentrun.status import STALE_AFTER_SEC, RunStatus, status_path, write_status
from issuekit.testing import FakeIssuekitClient
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


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
    fake_api,
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)
    old_heartbeat = (
        datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)
    ).replace(microsecond=0).isoformat()
    _write_run(tmp_path / ".agent-runs", "20261004-100003", issue=1, heartbeat_at=old_heartbeat)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" in payload
    assert "20261004-100003" in payload["stale_run"]


def test_show_flags_already_reconciled_abandoned_run(
    fake_api,
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)
    old_heartbeat = (
        datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)
    ).replace(microsecond=0).isoformat()
    write_status(
        status_path(tmp_path / ".agent-runs", "20261004-100003"),
        RunStatus(
            run_id="20261004-100003",
            agent="codex",
            issue=1,
            status="abandoned",
            pid=123,
            started_at=old_heartbeat,
            ended_at=old_heartbeat,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/20261004-100003.out.log",
            agent_log=".agent-runs/20261004-100003.agent.log",
            heartbeat_at=old_heartbeat,
            terminal_reason="heartbeat_lost",
        ),
    )

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" in payload
    assert "20261004-100003" in payload["stale_run"]


def test_show_does_not_flag_fresh_run(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)
    fresh_heartbeat = datetime.now().replace(microsecond=0).isoformat()
    _write_run(tmp_path / ".agent-runs", "20261004-100004", issue=1, heartbeat_at=fresh_heartbeat)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" not in payload


def test_show_json_includes_issue_priority(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", priority="high")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["priority"] == "high"


def test_show_ignores_stale_run_when_issue_not_implementing(
    fake_api,
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="review", assignee="claude")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)
    old_heartbeat = (
        datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)
    ).replace(microsecond=0).isoformat()
    _write_run(tmp_path / ".agent-runs", "20261004-100005", issue=1, heartbeat_at=old_heartbeat)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" not in payload


def test_show_is_best_effort_without_agent_runs_directory(
    fake_api,
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", stage="implementing", assignee="claude")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["show", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "stale_run" not in payload
