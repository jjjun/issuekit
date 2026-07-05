import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
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
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'issuekit'\n"
            "worker_id = 'operator'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *a, **k: client)
    monkeypatch.chdir(tmp_path)


def test_readdress_returns_directed_issue_to_repo_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(5, "Directed", target_worker="checkout.issuekit")]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["readdress", "5", "--reason", "stale directed checkout", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == 5
    assert payload["previous"]["target_worker"] == "checkout.issuekit"
    assert payload["expected_target_worker"] == "checkout.issuekit"
    assert payload["actor"] == "operator.issuekit"
    assert payload["audit_reason"] == "stale directed checkout"
    assert "target_worker" not in payload["issue"]
    assert client.get_issue(5)["target_worker"] == ""
    assert client.calls == [
        {
            "method": "readdress",
            "number": 5,
            "body": {
                "expected_target_worker": "checkout.issuekit",
                "actor": "operator.issuekit",
                "reason": "stale directed checkout",
            },
        }
    ]


def test_readdress_rejects_non_ascii_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(5, "Directed", target_worker="checkout.issuekit")]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["readdress", "5", "--reason", "stale \u2603"]) == 1

    assert "--reason must be ASCII-only" in capsys.readouterr().err
    assert client.calls == []
