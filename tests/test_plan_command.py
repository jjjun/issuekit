import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit.errors import WorkflowError
from issuekit.testing import FakeIssuekitClient
from tests.issue_helpers import api_issue


def _configure_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: FakeIssuekitClient,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_plan_holds_issue_by_default_and_excludes_it_from_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", stage="todo")])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["plan", "5", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "planned"
    assert client.get_issue(5)["stage"] == "planned"
    assert client.claim_next(assignee="codex") is None
    assert client.calls[0] == {
        "method": "plan",
        "number": 5,
        "body": {"stage": "planned"},
    }


def test_plan_releases_issue_to_todo_with_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Held", stage="planned")])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["plan", "5", "--stage", "todo", "--note", "human release"]) == 0

    assert "Planned issue #5: stage=todo" in capsys.readouterr().out
    assert client.get_issue(5)["stage"] == "todo"
    assert client.calls == [
        {
            "method": "plan",
            "number": 5,
            "body": {"stage": "todo", "note": "human release"},
        }
    ]


def test_plan_rejects_other_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Held", stage="planned")])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["plan", "5", "--stage", "review"]) == 2

    assert "invalid choice" in capsys.readouterr().err
    assert client.calls == []


def test_fake_plan_only_transitions_todo_or_planned_issues() -> None:
    client = FakeIssuekitClient([api_issue(5, "Working", stage="implementing")])

    with pytest.raises(WorkflowError, match="cannot be planned"):
        client.plan(5, stage="planned")
