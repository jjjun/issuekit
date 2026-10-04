from argparse import Namespace
from pathlib import Path

from issuekit import cli
from issuekit.commands.complete import run as run_complete
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


def test_complete_prefers_summary_and_verification_files_over_inline_text(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="todo")])
    configure_api(tmp_path, monkeypatch, fake_api, client)
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("File summary.\n", encoding="utf-8", newline="\n")
    verification_file = tmp_path / "verification.md"
    verification_file.write_text("File verification.\n", encoding="utf-8", newline="\n")

    exit_code = run_complete(
        Namespace(
            id="1",
            summary="Inline summary.",
            summary_file=str(summary_file),
            verification="Inline verification.",
            verification_file=str(verification_file),
            force=True,
        )
    )

    assert exit_code == 0
    assert client.calls[0]["body"] == {
        "summary": "File summary.",
        "verification": "File verification.",
        "force": True,
    }


def test_complete_rejects_invalid_issue_id(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient())

    exit_code = cli.main(["complete", "bad-id", "--summary", "Implemented."])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err
