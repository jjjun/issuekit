import json
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


def test_edit_command_updates_title_body_priority_and_prints_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Old title", body="Old body")])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "edit",
            "1",
            "--title",
            "New title",
            "--body",
            "New body",
            "--priority",
            "high",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["title"] == "New title"
    assert output["body"] == "New body"
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 1,
            "body": {"title": "New title", "body": "New body", "priority": "high"},
        }
    ]


def test_edit_command_replaces_dependency_refs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Depends", depends_on=["old#1"])])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["edit", "1", "--depends-on", "mine-py#42", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["depends_on"] == ["mine-py#42"]
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 1,
            "body": {"depends_on": ["mine-py#42"]},
        }
    ]


def test_edit_command_append_file_preserves_original_body(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Append", body="Original body")])
    append_file = tmp_path / "plan.md"
    append_file.write_text("## Implementation Plan\n\nDo this.\n", encoding="utf-8", newline="\n")
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["edit", "1", "--append-file", str(append_file)])

    assert exit_code == 0
    assert "Updated issue: demo#1" in capsys.readouterr().out
    assert client.get_issue(1)["body"] == "Original body\n\n## Implementation Plan\n\nDo this."


def test_edit_command_reports_missing_issue(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient()
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["edit", "99", "--title", "Missing"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err


def test_edit_command_requires_a_field(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "No-op")])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["edit", "1"])

    assert exit_code == 1
    assert "At least one of --title" in capsys.readouterr().err
    assert client.calls == []


def test_edit_command_rejects_non_ascii_input(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "ASCII")])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["edit", "1", "--body", "snowman \u2603"])

    assert exit_code == 1
    assert "--body and --body-file must be ASCII-only." in capsys.readouterr().err
    assert client.calls == []


def test_edit_command_requires_force_after_todo(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "In flight",
                status="in_progress",
                stage="implementing",
                assignee="codex",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["edit", "1", "--title", "Blocked"]) == 1
    assert "pass --force" in capsys.readouterr().err
    assert client.calls == []

    assert cli.main(["edit", "1", "--title", "Forced", "--force"]) == 0
    capsys.readouterr()
    assert client.get_issue(1)["title"] == "Forced"


def test_edit_command_refuses_completed_even_with_force(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Done", status="completed", stage="done", completed="2026-01-02")]
    )
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["edit", "1", "--title", "History rewrite", "--force"])

    assert exit_code == 1
    assert "completed and cannot be edited" in capsys.readouterr().err
    assert client.calls == []
