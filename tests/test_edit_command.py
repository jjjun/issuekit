import json
from pathlib import Path

from issuekit import cli
from issuekit.testing import FakeIssuekitClient
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


def test_edit_command_updates_title_body_priority_and_prints_json(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Old title", body="Old body")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(
        [
            "edit",
            "1",
            "--title",
            "New title",
            "--body",
            "New body",
            "--priority",
            "low",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["title"] == "New title"
    assert output["body"] == "New body"
    assert output["priority"] == "low"
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 1,
            "body": {"title": "New title", "body": "New body", "priority": "low"},
        }
    ]


def test_edit_command_replaces_dependency_refs(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Depends", depends_on=["old#1"])])
    configure_api(tmp_path, monkeypatch, fake_api, client)

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
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Append", body="Original body")])
    append_file = tmp_path / "plan.md"
    append_file.write_text("## Implementation Plan\n\nDo this.\n", encoding="utf-8", newline="\n")
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["edit", "1", "--append-file", str(append_file)])

    assert exit_code == 0
    assert "Updated issue: demo#1" in capsys.readouterr().out
    assert client.get_issue(1)["body"] == "Original body\n\n## Implementation Plan\n\nDo this."


def test_edit_command_append_uses_stored_body_without_rendered_workflow_sections(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    stored_body = "Original body"
    rendered_suffix = (
        "\n\n## Handoff\n\nImplemented by codex.\n\n"
        "## Review Feedback\n\nPlease correct the plan.\n"
    )
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Append",
                status="in_progress",
                stage="changes_requested",
                body=stored_body,
            )
        ],
        rendered_issue_suffixes={1: rendered_suffix},
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(
        ["edit", "1", "--append", "## Plan correction\n\nUpdated plan.", "--force"]
    )

    assert exit_code == 0
    capsys.readouterr()
    update = client.calls[-1]
    assert update == {
        "method": "update_issue",
        "number": 1,
        "body": {"body": f"{stored_body}\n\n## Plan correction\n\nUpdated plan."},
    }
    assert "## Handoff" not in update["body"]["body"]
    assert "## Review Feedback" not in update["body"]["body"]
    assert [call["method"] for call in client.calls] == ["get_issue_edit", "update_issue"]


def test_edit_command_title_only_does_not_read_stored_body(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Old title", body="Original body")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["edit", "1", "--title", "New title"])

    assert exit_code == 0
    capsys.readouterr()
    assert [call["method"] for call in client.calls] == ["update_issue"]


def test_fake_client_preserves_null_issue_body_without_rendered_suffix() -> None:
    client = FakeIssuekitClient([{"id": 1, "body": None}])

    assert client.get_issue(1)["body"] is None


def test_edit_command_reports_missing_issue(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient()
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["edit", "99", "--title", "Missing"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err


def test_edit_command_requires_a_field(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "No-op")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["edit", "1"])

    assert exit_code == 1
    assert "At least one of --title" in capsys.readouterr().err
    assert client.calls == []


def test_edit_command_rejects_non_ascii_input(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "ASCII")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["edit", "1", "--body", "snowman \u2603"])

    assert exit_code == 1
    assert "--body and --body-file must be ASCII-only." in capsys.readouterr().err
    assert client.calls == []


def test_edit_command_requires_force_after_todo(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
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
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["edit", "1", "--title", "Blocked"]) == 1
    assert "pass --force" in capsys.readouterr().err
    assert client.calls == []

    assert cli.main(["edit", "1", "--title", "Forced", "--force"]) == 0
    capsys.readouterr()
    assert client.get_issue(1)["title"] == "Forced"


def test_edit_command_allows_planned_issue_without_force(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Planned", stage="planned")])
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["edit", "1", "--title", "Updated plan"]) == 0

    capsys.readouterr()
    assert client.get_issue(1)["title"] == "Updated plan"
    assert [call["method"] for call in client.calls] == ["update_issue"]


def test_edit_command_refuses_completed_even_with_force(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Done", status="completed", stage="done", completed="2026-01-02")]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["edit", "1", "--title", "History rewrite", "--force"])

    assert exit_code == 1
    assert "completed and cannot be edited" in capsys.readouterr().err
    assert client.calls == []
