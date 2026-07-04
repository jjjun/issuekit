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


def test_reclaim_refuses_healthy_claim_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(5, "machine/issuekit/live")])
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_id="live", path="/repo"
    )
    client.calls.clear()
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["reclaim", "5", "--stale-after-sec", "999999999999"]) == 1

    captured = capsys.readouterr()
    assert "not currently flagged as an orphaned or stale claim" in captured.err
    assert [call["method"] for call in client.calls] == ["list_workers"]
    assert client.get_issue(5)["stage"] == "implementing"


def test_reclaim_force_proceeds_for_healthy_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(5, "machine/issuekit/live")])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["reclaim", "5", "--force"]) == 0

    assert (
        "Reclaimed issue #5: assignee=claude worker=machine/issuekit/live -> pool"
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
            "body": {"expected_worker": "machine/issuekit/live"},
        }
    ]


def test_reclaim_proceeds_for_stale_claim_with_expected_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([_implementing(6, "machine/issuekit/dead")])
    _configure_api(tmp_path, monkeypatch, client)

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
            "body": {"expected_worker": "machine/issuekit/dead"},
        },
    ]


def test_reclaim_rejects_non_implementing_target_even_with_force(
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
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["reclaim", "7", "--force"]) == 1

    assert "not implementing" in capsys.readouterr().err
    assert client.calls == []
