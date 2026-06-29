from pathlib import Path

from issuekit import cli
from issuekit.commands import migrate_to_api
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import issue_text, write_issue


def test_build_import_payload_preserves_legacy_metadata_and_body(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        (
            issue_text(
                1,
                "First",
                status="in_progress",
                priority="high",
                assignee="codex",
                stage="review",
                implementer="codex",
                author="claude",
            ).replace("title: First\n", "reviewer: kimi\norigin: mine#3@abc\ncustom: kept\n"
                "title: First\n")
            + "\n## Plan\n\nDo it.\n"
        ),
    )
    write_issue(
        issues_dir / "completed" / "002_done.md",
        issue_text(2, "Done", status="completed", completed="2026-01-02"),
    )

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert [issue["number"] for issue in payload] == [1, 2]
    first = payload[0]
    assert first["title"] == "First"
    assert first["status"] == "in_progress"
    assert first["priority"] == "high"
    assert first["assignee"] == "codex"
    assert first["stage"] == "review"
    assert first["implementer"] == "codex"
    assert first["author"] == "claude"
    assert first["reviewer"] == "kimi"
    assert first["origin"] == "mine#3@abc"
    assert first["extra"] == {"custom": "kept"}
    assert first["body"].startswith("# Issue #1: First\n")
    assert "---" not in first["body"]


def test_build_import_payload_defaults_missing_active_stage_to_todo(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert payload[0]["stage"] == "todo"


def test_build_import_payload_defaults_missing_completed_stage_to_done(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "completed" / "001_done.md",
        issue_text(1, "Done", status="completed", completed="2026-01-02"),
    )

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert payload[0]["status"] == "completed"
    assert payload[0]["stage"] == "done"


def test_build_import_payload_preserves_explicit_stage_for_completed_issue(
    tmp_path: Path,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "completed" / "001_done.md",
        issue_text(
            1,
            "Done",
            status="completed",
            completed="2026-01-02",
            stage="review",
        ),
    )

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert payload[0]["stage"] == "review"


def test_migrate_to_api_dry_run_does_not_require_api_url(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["migrate-to-api", "--dry-run"])

    assert exit_code == 0
    assert "Dry run: built import payload for 1 issue(s)" in capsys.readouterr().out


def test_migrate_to_api_import_is_rerunnable_with_fake_client(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient()
    monkeypatch.setattr(migrate_to_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["migrate-to-api"]) == 0
    assert cli.main(["migrate-to-api"]) == 0

    assert [issue["id"] for issue in client.list_issues()] == [1]
    assert [call["method"] for call in client.calls] == ["import_issues", "import_issues"]
    assert "Migrated 1 issue(s) to project demo." in capsys.readouterr().out


def test_migrate_to_api_verifies_completed_imports_with_fake_client(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "completed" / "001_done.md",
        issue_text(1, "Done", status="completed", completed="2026-01-02"),
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient()
    monkeypatch.setattr(migrate_to_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["migrate-to-api"]) == 0

    assert client.list_issues() == []
    assert [issue["id"] for issue in client.list_issues(status="completed")] == [1]
    assert "Migrated 1 issue(s) to project demo." in capsys.readouterr().out
