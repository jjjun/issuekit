import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.testing import FakeIssuekitClient
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


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


def test_reclaim_refuses_healthy_claim_without_force(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(5, "live.issuekit@machine")])
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_name="live", path="/repo"
    )
    client.calls.clear()
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["reclaim", "5", "--stale-after-sec", "999999999999"]) == 1

    captured = capsys.readouterr()
    assert "not currently flagged as an orphaned or stale claim" in captured.err
    assert [call["method"] for call in client.calls] == ["list_workers"]
    assert client.get_issue(5)["stage"] == "implementing"


def test_reclaim_force_proceeds_for_healthy_claim(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(5, "live.issuekit@machine")])
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["reclaim", "5", "--force"]) == 0

    assert (
        "Reclaimed issue #5: assignee=claude worker=live.issuekit@machine -> pool"
        in capsys.readouterr().out
    )
    issue = client.get_issue(5)
    assert issue["status"] == "active"
    assert issue["stage"] == "todo"
    assert issue["assignee"] == ""
    assert issue["implementer"] == ""
    assert issue["worker"] == ""
    assert client.calls == [
        {
            "method": "reclaim",
            "number": 5,
            "body": {"expected_worker": "live.issuekit@machine", "actor": "issuekit"},
        }
    ]


def test_reclaim_proceeds_for_stale_claim_with_expected_worker(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(6, "machine/issuekit/dead")])
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["reclaim", "6", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == 6
    assert payload["previous"] == {
        "assignee": "claude",
        "worker": "machine/issuekit/dead",
        "stage": "implementing",
    }
    assert payload["expected_worker"] == "machine/issuekit/dead"
    assert payload["reason"] == "no_worker"
    assert payload["issue"]["status"] == "active"
    assert payload["issue"]["stage"] == "todo"
    assert payload["issue"]["assignee"] == ""
    assert payload["issue"]["worker"] == ""
    assert client.calls == [
        {
            "method": "list_workers",
            "body": {"repo_id": None, "project": None},
        },
        {
            "method": "reclaim",
            "number": 6,
            "body": {"expected_worker": "machine/issuekit/dead", "actor": "issuekit"},
        },
    ]


def test_reclaim_forwards_reason(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient([_implementing(6, "machine/issuekit/dead")])
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["reclaim", "6", "--force", "--reason", "stale checkout"]) == 0

    assert client.calls == [
        {
            "method": "reclaim",
            "number": 6,
            "body": {
                "expected_worker": "machine/issuekit/dead",
                "actor": "issuekit",
                "reason": "stale checkout",
            },
        }
    ]


def test_reclaim_rejects_non_ascii_reason(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(6, "machine/issuekit/dead")])
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["reclaim", "6", "--force", "--reason", "stale \u2603"]) == 1

    assert "--reason must be ASCII-only" in capsys.readouterr().err
    assert client.calls == []


def test_reclaim_rejects_non_implementing_target_even_with_force(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Review",
                status="in_progress",
                stage="review",
                assignee="claude",
                worker="machine/issuekit/dead",
            )
        ]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit")

    assert cli.main(["reclaim", "7", "--force"]) == 1

    assert "not implementing" in capsys.readouterr().err
    assert client.calls == []
