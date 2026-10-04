"""Tests for the `issuekit profile` command."""

from __future__ import annotations

import json
from pathlib import Path

from issuekit import cli
from issuekit.testing import FakeIssuekitClient


def _write_config(tmp_path: Path, monkeypatch, *, api_url: bool = True) -> None:
    lines = ["project = 'issuekit'\n"]
    if api_url:
        lines.insert(0, "api_url = 'https://mine.example'\n")
        machine_path = tmp_path / "machine.toml"
        machine_path.write_text(
            "trusted_api_origins = ['https://mine.example']\n",
            encoding="utf-8",
            newline="\n",
        )
        monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / "issuekit.toml").write_text("".join(lines), encoding="utf-8", newline="\n")


def _seed_profiles(fake_api, monkeypatch, client: FakeIssuekitClient) -> None:
    client.project = "issuekit"
    client.put_project_profile(summary="Workflow CLI.", profile_md="# issuekit\n", tags=["python"])
    client.project = "mine-py"
    client.put_project_profile(summary="Issue API.", profile_md="# mine-py\n", tags=["api"])
    client.project = "issuekit"
    fake_api.install_client(client)


def test_profile_local_only_without_api(monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path, monkeypatch, api_url=False)
    (tmp_path / "ISSUEKIT.md").write_text(
        "# issuekit\n\nProfile body.\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["profile"]) == 0
    out = capsys.readouterr().out
    assert "Local project profile (ISSUEKIT.md):" in out
    assert "profile_md:" in out


def test_profile_local_absent_message(monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path, monkeypatch, api_url=False)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["profile"]) == 0
    assert "No local project profile" in capsys.readouterr().out


def test_profile_local_json_includes_local_and_remote(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path, monkeypatch)
    (tmp_path / "ISSUEKIT.md").write_text("# issuekit\n", encoding="utf-8", newline="\n")
    client = FakeIssuekitClient()
    _seed_profiles(fake_api, monkeypatch, client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["profile", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["local"]["profile_md"] == "# issuekit\n"
    assert payload["remote"]["summary"] == "Workflow CLI."


def test_profile_project_fetches_remote(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path, monkeypatch)
    client = FakeIssuekitClient()
    _seed_profiles(fake_api, monkeypatch, client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["profile", "--project", "mine-py", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"] == "mine-py"
    assert payload["summary"] == "Issue API."


def test_profile_all_lists_every_profile(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path, monkeypatch)
    client = FakeIssuekitClient()
    _seed_profiles(fake_api, monkeypatch, client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["profile", "--all", "--json"]) == 0
    profiles = json.loads(capsys.readouterr().out)
    assert {profile["project"] for profile in profiles} == {"issuekit", "mine-py"}


def test_profile_local_tolerates_remote_unsupported(fake_api, monkeypatch, tmp_path, capsys) -> None:
    # Backend without mine-py#172: remote GET 404s, local profile still shown.
    _write_config(tmp_path, monkeypatch)
    (tmp_path / "ISSUEKIT.md").write_text("# issuekit\n", encoding="utf-8", newline="\n")
    client = FakeIssuekitClient()  # no profiles seeded -> get raises http_404
    fake_api.install_client(client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["profile", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["local"]["profile_md"] == "# issuekit\n"
    assert payload["remote"] is None
