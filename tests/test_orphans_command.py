import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit import worker_registry
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


def _configure_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: FakeIssuekitClient,
    *,
    project: str = "issuekit",
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"api_url = 'https://mine.example'\nproject = '{project}'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *a, **k: client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *a, **k: client)
    monkeypatch.chdir(tmp_path)


def _implementing(issue_id: int, worker: str, *, assignee: str = "claude") -> dict:
    return api_issue(
        issue_id,
        f"Task {issue_id}",
        status="in_progress",
        stage="implementing",
        assignee=assignee,
        implementer=assignee,
        worker=worker,
    )


def test_orphans_flags_claim_without_live_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(5, "machine/issuekit/dead")])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["orphans"]) == 0

    out = capsys.readouterr().out
    assert "Orphaned or stale implementing claims: 1" in out
    assert "#5" in out
    assert "no live registered worker" in out
    assert "worker=machine/issuekit/dead" in out


def test_orphans_json_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(5, "machine/issuekit/dead")])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["orphans", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "id": 5,
            "ref": "issuekit#5",
            "title": "Task 5",
            "assignee": "claude",
            "worker": "machine/issuekit/dead",
            "reason": "no_worker",
            "last_seen": None,
            "stale_seconds": None,
        }
    ]


def test_orphans_flags_expired_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(6, "machine/issuekit/slow")])
    # Registered worker heartbeat is fixed at 2026-01-01, far older than "now".
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_id="slow", path="/repo"
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["orphans", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["id"] == 6
    assert payload[0]["reason"] == "expired_heartbeat"
    assert payload[0]["last_seen"] == "2026-01-01T00:00:00Z"


def test_orphans_healthy_worker_within_window_is_not_flagged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(6, "machine/issuekit/slow")])
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_id="slow", path="/repo"
    )
    _configure_api(tmp_path, monkeypatch, client)

    # A huge window keeps the fixed 2026-01-01 heartbeat inside the live window.
    assert cli.main(["orphans", "--stale-after-sec", "999999999999"]) == 0

    assert "No orphaned or stale implementing claims." in capsys.readouterr().out


def test_orphans_ignores_non_implementing_and_unclaimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Review", status="in_progress", stage="review",
                      worker="machine/issuekit/dead"),
            _implementing(2, ""),  # implementing but no recorded worker
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["orphans"]) == 0

    assert "No orphaned or stale implementing claims." in capsys.readouterr().out


def test_orphans_requires_api_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ISSUEKIT_API_URL", raising=False)
    (tmp_path / "issuekit.toml").write_text(
        "project = 'issuekit'\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["orphans"]) == 1

    assert "requires api_url" in capsys.readouterr().err
