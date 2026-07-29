from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.testing import FakeIssuekitClient
from tests.issue_helpers import api_issue


def _configure_api(tmp_path: Path, monkeypatch, client: FakeIssuekitClient) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_complete_command_calls_api_without_validating_afterwards(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="review")])
    _configure_api(tmp_path, monkeypatch, client)

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


def test_complete_missing_issue_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["complete", "999"])

    assert exit_code == 1
    assert "Issue #999 was not found" in capsys.readouterr().err


def test_complete_rejects_non_ascii_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient([api_issue(1, "First")]))

    exit_code = cli.main(["complete", "1", "--summary", "\u3042"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err


def test_complete_force_closes_todo_issue(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="todo")])
    _configure_api(tmp_path, monkeypatch, client)

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


def test_complete_rejects_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["complete", "bad-id", "--summary", "Implemented."])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err
