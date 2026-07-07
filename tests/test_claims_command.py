import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module

from tests.issue_helpers import api_issue


def _configure_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_claims_command_lists_active_claims_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit.testing import FakeIssuekitClient

    implementing = api_issue(
        1,
        "Implement",
        status="in_progress",
        stage="implementing",
        assignee="codex",
        worker="codex.demo",
    )
    implementing["claimed_at"] = "2026-07-07T01:02:03Z"
    review = api_issue(
        2,
        "Review",
        status="in_progress",
        stage="review",
        assignee="claude",
        target_worker="codex.demo",
    )
    review["worker"] = None
    review["implementation_worker"] = "codex.demo"
    review["updated_at"] = "2026-07-07T02:03:04Z"
    client = FakeIssuekitClient(
        [
            implementing,
            review,
            api_issue(3, "Ready", stage="todo", worker="codex.demo"),
            api_issue(
                4,
                "Done",
                status="completed",
                stage="review",
                worker="codex.demo",
            ),
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["claims", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "id": 1,
            "ref": "demo#1",
            "title": "Implement",
            "stage": "implementing",
            "assignee": "codex",
            "worker": "codex.demo",
            "target_worker": "",
            "claimed": "2026-07-07T01:02:03Z",
        },
        {
            "id": 2,
            "ref": "demo#2",
            "title": "Review",
            "stage": "review",
            "assignee": "claude",
            "worker": "codex.demo",
            "target_worker": "codex.demo",
            "last_transition": "2026-07-07T02:03:04Z",
        },
    ]


def test_claims_command_filters_worker_with_qualified_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit.testing import FakeIssuekitClient

    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Mine",
                status="in_progress",
                stage="implementing",
                worker="bob.mine-py@pike3",
            ),
            api_issue(
                2,
                "Other",
                status="in_progress",
                stage="implementing",
                worker="ann.mine-py@pike3",
            ),
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["claims", "--worker", "bob.mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in payload] == [1]


def test_claims_command_filters_worker_with_review_implementation_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit.testing import FakeIssuekitClient

    review = api_issue(
        1,
        "Review",
        status="in_progress",
        stage="review",
        assignee="claude",
    )
    review["worker"] = None
    review["implementation_worker"] = "bob.mine-py@pike3"
    other = api_issue(
        2,
        "Other",
        status="in_progress",
        stage="review",
        assignee="claude",
    )
    other["worker"] = None
    other["implementation_worker"] = "ann.mine-py@pike3"
    client = FakeIssuekitClient([review, other])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["claims", "--worker", "bob.mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "id": 1,
            "ref": "demo#1",
            "title": "Review",
            "stage": "review",
            "assignee": "claude",
            "worker": "bob.mine-py@pike3",
            "target_worker": "",
        }
    ]


def test_claims_command_lists_changes_requested_implementation_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit.testing import FakeIssuekitClient

    changes_requested = api_issue(
        1,
        "Needs changes",
        status="in_progress",
        stage="changes_requested",
        assignee="codex",
    )
    changes_requested["worker"] = None
    changes_requested["implementation_worker"] = "bob.mine-py@pike3"
    other = api_issue(
        2,
        "Other changes",
        status="in_progress",
        stage="changes_requested",
        assignee="codex",
    )
    other["worker"] = None
    other["implementation_worker"] = "ann.mine-py@pike3"
    client = FakeIssuekitClient([changes_requested, other])
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["claims", "--json"]) == 0

    all_payload = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in all_payload] == [1, 2]
    assert all_payload[0]["stage"] == "changes_requested"
    assert all_payload[0]["worker"] == "bob.mine-py@pike3"

    assert cli.main(["claims", "--worker", "bob.mine-py", "--json"]) == 0

    filtered_payload = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in filtered_payload] == [1]


def test_claims_command_filters_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit.testing import FakeIssuekitClient

    review = api_issue(
        2,
        "Review",
        status="in_progress",
        stage="review",
    )
    review["worker"] = None
    review["implementation_worker"] = "codex.demo"
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Implement",
                status="in_progress",
                stage="implementing",
                worker="codex.demo",
            ),
            review,
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["claims", "--stage", "review"]) == 0

    out = capsys.readouterr().out
    assert "#2: Review" in out
    assert "#1: Implement" not in out


def test_claims_command_handles_empty_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit.testing import FakeIssuekitClient

    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    assert cli.main(["claims"]) == 0

    assert "No active worker claims." in capsys.readouterr().out
