import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit.testing import FakeIssuekitClient
from issuekit.workers import registry as worker_registry
from tests.issue_helpers import api_issue


def _configure_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_workers_command_lists_registered_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_id="checkout",
        path="/repo",
        role="api-server",
        description="Hosts the mine-py issue API.",
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers"]) == 0

    out = capsys.readouterr().out
    assert "checkout.mine-py  role=api-server" in out
    assert "machine=machine" in out
    assert "Hosts the mine-py issue API." in out


def test_workers_command_json_and_repo_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine", repo_id="mine-py", worker_id="c1", path="/a", role="api"
    )
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_id="c2", path="/b", role="cli"
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers", "--repo-id", "mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [row["repo_id"] for row in payload] == ["mine-py"]
    assert client.calls[-1] == {
        "method": "list_workers",
        "body": {"repo_id": "mine-py", "project": None},
    }


def test_workers_command_prints_repo_and_worker_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
        repo_description="Mine API service.",
        repo_metadata={"domain": "api"},
        worker_metadata={"queue": "fast"},
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers"]) == 0

    out = capsys.readouterr().out
    assert "repo: Mine API service." in out
    assert "repo_metadata: domain=api" in out
    assert "worker_metadata: queue=fast" in out


def test_workers_command_reports_missing_api_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ISSUEKIT_API_URL", raising=False)
    (tmp_path / "issuekit.toml").write_text(
        "project = 'demo'\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["workers"]) == 1

    assert "requires api_url" in capsys.readouterr().err


def test_workers_command_handles_empty_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers"]) == 0

    assert "No workers registered." in capsys.readouterr().out


def test_workers_remove_deletes_by_dotted_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers", "remove", "checkout.mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["display"] == "checkout.mine-py"
    assert payload["deleted"] == {"id": "checkout.mine-py", "deleted": True}
    assert [call["method"] for call in client.calls[-2:]] == [
        "list_workers",
        "delete_worker",
    ]


def test_workers_remove_rejects_legacy_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers", "remove", "machine/mine-py/checkout", "--json"]) == 1

    assert "Worker was not found" in capsys.readouterr().err


def test_workers_remove_refuses_implementing_holder_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="checkout.mine-py",
            )
        ]
    )
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers", "remove", "checkout.mine-py"]) == 1

    assert "holds implementing issue(s) #7" in capsys.readouterr().err
    assert "delete_worker" not in [call["method"] for call in client.calls]


def test_workers_remove_force_deletes_implementing_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="checkout.mine-py@machine",
            )
        ]
    )
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers", "remove", "checkout.mine-py", "--force", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["implementing_issues"][0]["id"] == 7
    assert client.calls[-1]["method"] == "delete_worker"


def test_workers_prune_dry_run_filters_to_stale_issueless_untargeted_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                10,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="held.mine-py",
            ),
            api_issue(
                11,
                "Directed",
                stage="todo",
                target_worker="targeted.mine-py",
            ),
        ]
    )
    for worker in ("stale", "held", "targeted", "fresh"):
        client.upsert_worker(
            machine_id="machine",
            repo_id="mine-py",
            worker_name=worker,
            path=f"/{worker}",
        )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    client._workers["held.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    client._workers["targeted.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    client._workers["fresh.mine-py"]["last_seen"] = "2999-01-01T00:00:00Z"
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["workers", "prune", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["display"] for item in payload["candidates"]] == ["stale.mine-py"]
    assert "delete_worker" not in [call["method"] for call in client.calls]


def test_workers_prune_warns_when_staleness_is_not_wider_than_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    assert (
        cli.main(
            ["workers", "prune", "--stale-after-sec", "60", "--dry-run"]
        )
        == 0
    )

    assert "healthy worker may appear stale between beats" in capsys.readouterr().err


def test_workers_prune_requires_count_confirmation_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="stale",
        path="/stale",
    )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("builtins.input", lambda prompt: "1")

    assert cli.main(["workers", "prune", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["deleted"] == [{"id": "stale.mine-py", "deleted": True}]
    assert client.calls[-1] == {
        "method": "delete_worker",
        "body": {"id": "stale.mine-py"},
    }
