from pathlib import Path

from issuekit import cli
from issuekit.testing import FakeIssuekitClient
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


def test_complete_command_calls_api_without_validating_afterwards(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="review")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(
        [
            "complete",
            "1",
            "--summary",
            "Implemented the command.",
            "--verification",
            "uv run pytest",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Completed issue #1: demo#1" in captured.out
    assert "API validation passed" not in captured.out
    assert client.get_issue(1)["status"] == "completed"
    assert client.calls[0] == {
        "method": "complete",
        "number": 1,
        "body": {
            "summary": "Implemented the command.",
            "verification": "uv run pytest",
            "force": False,
        },
    }


def test_complete_missing_issue_fails(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient())

    exit_code = cli.main(["complete", "999"])

    assert exit_code == 1
    assert "Issue #999 was not found" in capsys.readouterr().err


def test_complete_rejects_non_ascii_summary(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient([api_issue(1, "First")]))

    exit_code = cli.main(["complete", "1", "--summary", "\u3042"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err


def test_complete_force_closes_todo_issue(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="todo")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(
        [
            "complete",
            "1",
            "--force",
            "--summary",
            "Closing obsolete anchor.",
            "--verification",
            "no local code scope",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Completed issue #1" in captured.out
    assert client.get_issue(1)["status"] == "completed"
    assert client.calls[0]["method"] == "complete"
    assert client.calls[0]["body"]["force"] is True


def test_complete_rejects_invalid_issue_id(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient())

    exit_code = cli.main(["complete", "bad-id", "--summary", "Implemented."])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err
