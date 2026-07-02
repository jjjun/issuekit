import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import worker_registry
from issuekit.testing import FakeIssuekitClient


def _configure_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
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
    assert "machine/mine-py/checkout  role=api-server" in out
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
