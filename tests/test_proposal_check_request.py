"""Tests for requesting proposal checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import issuekit.proposals.api as proposals_api
from issuekit import cli
from issuekit.testing import FakeIssuekitClient


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    workers: tuple[tuple[str, str], ...],
    repo_id: str = "target",
) -> FakeIssuekitClient:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 7,
                "origin": "source#7@abc",
                "title": "Check this",
                "body": "Evaluate before adoption.",
            }
        ]
    )
    for worker_name, machine_id in workers:
        client.upsert_worker(
            machine_id=machine_id,
            repo_id=repo_id,
            worker_name=worker_name,
            project="target",
        )
    client.calls.clear()
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    return client


def test_cli_request_auto_selects_single_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _setup(tmp_path, monkeypatch, workers=(("worker", "machine"),))

    assert (
        cli.main(
            [
                "proposal-check-request",
                "--to",
                "target",
                "--proposal",
                "7",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert result["id"] == 1
    assert result["target_worker"] == "worker.target@machine"
    assert result["was_created"] is True
    assert result["worker_auto_selected"] is True
    assert client.calls[-1] == {
        "method": "create_proposal_check",
        "number": 7,
        "body": {
            "target_worker": "worker.target@machine",
            "project": "target",
        },
    }


def test_cli_request_filters_workers_by_project_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _setup(
        tmp_path,
        monkeypatch,
        workers=(("worker", "machine"),),
        repo_id="checkout",
    )

    assert (
        cli.main(
            [
                "proposal-check-request",
                "--to",
                "target",
                "--proposal",
                "7",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert result["target_worker"] == "worker.checkout@machine"
    assert client.calls[0] == {
        "method": "list_workers",
        "body": {"repo_id": None, "project": "target"},
    }


def test_cli_request_requires_worker_when_multiple_are_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _setup(
        tmp_path,
        monkeypatch,
        workers=(("alpha", "main1"), ("beta", "pike3")),
    )

    assert (
        cli.main(
            [
                "proposal-check-request",
                "--to",
                "target",
                "--proposal",
                "7",
            ]
        )
        == 1
    )
    error = capsys.readouterr().err

    assert "Pass --worker with one of:" in error
    assert "alpha.target@main1" in error
    assert "beta.target@pike3" in error
    assert "status=idle" in error
    assert "last_seen=2026-01-01T00:00:00Z" in error
    assert not any(call["method"] == "create_proposal_check" for call in client.calls)


def test_cli_request_warns_for_offline_worker_and_still_creates_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _setup(tmp_path, monkeypatch, workers=(("worker", "machine"),))
    client._workers["worker.target"]["status"] = "offline"
    client._workers["worker.target"]["last_seen"] = "2026-07-01T00:00:00Z"

    assert (
        cli.main(
            [
                "proposal-check-request",
                "--to",
                "target",
                "--proposal",
                "7",
                "--worker",
                "worker.target@machine",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Created proposal check #1" in captured.out
    assert "may be unreachable" in captured.err
    assert "status=offline" in captured.err
    assert "last_seen=2026-07-01T00:00:00Z" in captured.err
    assert client._proposal_checks[1]["status"] == "pending"


def test_cli_request_normalizes_and_validates_explicit_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _setup(
        tmp_path,
        monkeypatch,
        workers=(("alpha", "main1"), ("beta", "pike3")),
    )

    assert (
        cli.main(
            [
                "proposal-check-request",
                "--to",
                "target",
                "--proposal",
                "7",
                "--worker",
                " beta.target@pike3 ",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Created proposal check #1: target_worker=beta.target@pike3" in output
    assert "Automatically selected" not in output
    assert client._proposal_checks[1]["target_worker"] == "beta.target@pike3"

    assert (
        cli.main(
            [
                "proposal-check-request",
                "--to",
                "target",
                "--proposal",
                "7",
                "--worker",
                "missing.target",
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "Worker is not registered" in error
    assert "alpha.target@main1" in error
    assert "beta.target@pike3" in error


def test_cli_request_reports_existing_pending_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup(tmp_path, monkeypatch, workers=(("worker", "machine"),))
    argv = [
        "proposal-check-request",
        "--to",
        "target",
        "--proposal",
        "7",
        "--worker",
        "worker.target@machine",
        "--json",
    ]

    assert cli.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(argv) == 0
    second = json.loads(capsys.readouterr().out)

    assert second["id"] == first["id"]
    assert first["was_created"] is True
    assert second["was_created"] is False
