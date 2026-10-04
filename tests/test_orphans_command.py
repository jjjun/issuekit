import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.testing import FakeIssuekitClient
from tests.agent_fakes import implementing_issue
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


def test_orphans_flags_claim_without_live_worker(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([implementing_issue(5, "dead.issuekit@machine")])
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["orphans"]) == 0

    out = capsys.readouterr().out
    assert "Orphaned or stale implementing claims: 1" in out
    assert "#5" in out
    assert "no live registered worker" in out
    assert "worker=dead.issuekit@machine" in out


def test_orphans_json_shape(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([implementing_issue(5, "dead.issuekit@machine")])
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["orphans", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "id": 5,
            "ref": "issuekit#5",
            "title": "Task 5",
            "assignee": "claude",
            "worker": "dead.issuekit@machine",
            "reason": "no_worker",
            "last_seen": None,
            "stale_seconds": None,
        }
    ]


def test_orphans_json_includes_stale_directed_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(8, "Directed", stage="todo", target_worker="checkout.issuekit")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["orphans", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "id": 8,
            "ref": "issuekit#8",
            "title": "Directed",
            "assignee": "",
            "worker": "checkout.issuekit",
            "reason": "directed_no_worker",
            "last_seen": None,
            "stale_seconds": None,
            "target_worker": "checkout.issuekit",
        }
    ]


def test_orphans_flags_expired_heartbeat(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([implementing_issue(6, "slow.issuekit@machine")])
    # Registered worker heartbeat is fixed at 2026-01-01, far older than "now".
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_name="slow", path="/repo"
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["orphans", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["id"] == 6
    assert payload[0]["reason"] == "expired_heartbeat"
    assert payload[0]["last_seen"] == "2026-01-01T00:00:00Z"


def test_orphans_healthy_worker_within_window_is_not_flagged(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([implementing_issue(6, "slow.issuekit@machine")])
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_name="slow", path="/repo"
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    # A huge window keeps the fixed 2026-01-01 heartbeat inside the live window.
    assert cli.main(["orphans", "--stale-after-sec", "999999999999"]) == 0

    assert "No orphaned or stale implementing claims." in capsys.readouterr().out


def test_orphans_warns_when_staleness_is_not_wider_than_heartbeat(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient(), project="issuekit")

    assert cli.main(["orphans", "--stale-after-sec", "60"]) == 0

    assert "healthy worker may appear stale between beats" in capsys.readouterr().err


def test_orphans_ignores_non_implementing_and_unclaimed(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Review", status="in_progress", stage="review",
                      worker="dead.issuekit@machine"),
            implementing_issue(2, ""),  # implementing but no recorded worker
        ]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

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
