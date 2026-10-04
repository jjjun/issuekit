import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.testing import FakeIssuekitClient
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


def test_readdress_returns_directed_issue_to_repo_pool(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(5, "Directed", target_worker="checkout.issuekit")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit", worker=True)

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
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [api_issue(5, "Directed", target_worker="checkout.issuekit")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client, project="issuekit", worker=True)

    assert cli.main(["readdress", "5", "--reason", "stale \u2603"]) == 1

    assert "--reason must be ASCII-only" in capsys.readouterr().err
    assert client.calls == []
