import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.testing import FakeIssuekitClient
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


def _register_worker(client: FakeIssuekitClient, *, machine: str = "machine") -> None:
    client.upsert_worker(
        machine_id=machine,
        repo_id="demo",
        worker_name="checkout",
        project="demo",
    )
    client.calls.clear()


def test_dispatch_directs_ready_issue_and_reports_stored_target(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(5, "Ready", stage="todo")],
        stored_target_worker_override="server-stored.synthetic@response",
    )
    _register_worker(client)
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert (
        cli.main(
            [
                "dispatch",
                "5",
                "--target-worker",
                "checkout.demo@machine",
                "--assignee",
                "codex",
                "--stage",
                "planned",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["target_worker"] == "server-stored.synthetic@response"
    assert payload["assignee"] == "codex"
    assert payload["stage"] == "planned"
    assert client.calls == [
        {
            "method": "list_workers",
            "body": {"repo_id": "demo", "project": "demo"},
        },
        {
            "method": "dispatch",
            "number": 5,
            "body": {
                "target_worker": "checkout.demo@machine",
                "assignee": "codex",
                "stage": "planned",
            },
        },
    ]


def test_dispatch_rejects_unregistered_worker_before_mutation(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", stage="todo")])
    _register_worker(client, machine="other")
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert (
        cli.main(
            [
                "dispatch",
                "5",
                "--target-worker",
                "checkout.demo@machine",
            ]
        )
        == 1
    )

    assert "Target worker is not registered" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["list_workers"]


def test_dispatch_allows_explicit_unregistered_override(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", stage="todo")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert (
        cli.main(
            [
                "dispatch",
                "5",
                "--target-worker",
                "future.demo@machine",
                "--allow-unregistered-worker",
            ]
        )
        == 0
    )
    assert client.get_issue(5)["target_worker"] == "future.demo@machine"


def test_dispatch_refuses_implementing_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(5, "Held", stage="implementing", worker="checkout.demo")]
    )
    _register_worker(client)
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert (
        cli.main(["dispatch", "5", "--target-worker", "checkout.demo@machine"])
        == 1
    )

    assert "not dispatchable" in capsys.readouterr().err
