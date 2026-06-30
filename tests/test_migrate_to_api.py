from pathlib import Path

import pytest

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


def test_build_import_payload_converts_empty_dates_to_none(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", created="", completed=""),
    )

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert payload[0]["created"] is None
    assert payload[0]["completed"] is None


def test_build_import_payload_preserves_non_empty_dates(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "completed" / "001_done.md",
        issue_text(
            1,
            "Done",
            status="completed",
            created="2026-01-01",
            completed="2026-01-02",
        ),
    )

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert payload[0]["created"] == "2026-01-01"
    assert payload[0]["completed"] == "2026-01-02"


def test_build_import_payload_has_no_empty_date_strings(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", created="", completed=""),
    )
    write_issue(
        issues_dir / "completed" / "002_done.md",
        issue_text(2, "Done", status="completed", completed="2026-01-02"),
    )

    payload = migrate_to_api.build_import_payload(issues_dir)

    assert all(issue["created"] != "" for issue in payload)
    assert all(issue["completed"] != "" for issue in payload)


def test_build_proposal_import_payload_maps_legacy_inboxes(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    _write_proposal_file(
        issues_dir / "incoming" / "pending.md",
        origin="source#1@abc123",
        created="",
        title="Pending",
        body="Pending body.",
    )
    _write_proposal_file(
        issues_dir / "incoming" / "adopted" / "adopted.md",
        origin="source#2@abc123",
        created="2026-06-01",
        title="Adopted",
        body="Adopted body.",
    )
    _write_proposal_file(
        issues_dir / "incoming" / "discarded" / "discarded.md",
        origin="source#3@abc123",
        reply_to="source#0@old",
        created="2026-06-02",
        title="Discarded",
        body="Discarded body.",
    )
    write_issue(
        issues_dir / "active" / "004_adopted.md",
        issue_text(4, "Adopted").replace("title: Adopted\n", "origin: source#2@abc123\ntitle: Adopted\n"),
    )

    payload = migrate_to_api.build_proposal_import_payload(issues_dir)

    assert [(item["title"], item["status"]) for item in payload] == [
        ("Pending", "pending"),
        ("Adopted", "adopted"),
        ("Discarded", "discarded"),
    ]
    assert payload[0]["created"] is None
    assert payload[0]["reply_to"] is None
    assert payload[1]["adopted_issue_number"] == 4
    assert payload[2]["reply_to"] == "source#0@old"


def test_verify_proposal_import_allows_duplicate_adopted_origins() -> None:
    source = [
        _proposal_payload("source#0@abc123", "adopted", "First"),
        _proposal_payload("source#0@abc123", "adopted", "Second"),
    ]
    stored = [
        {**source[0], "id": 1},
        {**source[1], "id": 2},
    ]

    migrate_to_api.verify_proposal_import(source, stored)


def test_verify_proposal_import_rejects_duplicate_pending_origins() -> None:
    source = [
        _proposal_payload("source#0@abc123", "pending", "First"),
        _proposal_payload("source#0@abc123", "pending", "Second"),
    ]

    with pytest.raises(ValueError, match="duplicate pending proposal origin"):
        migrate_to_api.verify_proposal_import(source, source)


def test_verify_proposal_import_rejects_missing_source_proposal() -> None:
    source = [
        _proposal_payload("source#0@abc123", "adopted", "First"),
        _proposal_payload("source#0@abc123", "adopted", "Second"),
    ]
    stored = [{**source[0], "id": 1}]

    with pytest.raises(ValueError, match="Imported proposal\\(s\\) missing from response"):
        migrate_to_api.verify_proposal_import(source, stored)


def test_migrate_proposals_to_api_dry_run_does_not_require_api_url(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    _write_proposal_file(
        issues_dir / "incoming" / "pending.md",
        origin="source#1@abc123",
        title="Pending",
        body="Pending body.",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["migrate-proposals-to-api", "--dry-run"])

    assert exit_code == 0
    assert "Dry run: built proposal import payload for 1 proposal(s)" in capsys.readouterr().out


def test_migrate_proposals_to_api_import_is_rerunnable_with_fake_client(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    _write_proposal_file(
        issues_dir / "incoming" / "pending.md",
        origin="source#1@abc123",
        title="Pending",
        body="Pending body.",
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient()
    monkeypatch.setattr(migrate_to_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["migrate-proposals-to-api"]) == 0
    assert cli.main(["migrate-proposals-to-api"]) == 0

    assert [proposal["title"] for proposal in client.list_proposals(status="pending")] == ["Pending"]
    assert [call["method"] for call in client.calls] == ["import_proposals", "import_proposals"]
    assert "Migrated 1 proposal(s) to project demo." in capsys.readouterr().out


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


def test_migrate_to_api_verification_reads_all_completed_pages(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    for issue_id in range(1, 126):
        write_issue(
            issues_dir / "completed" / f"{issue_id:03d}_done.md",
            issue_text(
                issue_id,
                f"Done {issue_id}",
                status="completed",
                completed="2026-01-02",
            ),
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

    assert len(client.list_all_issues(status="completed")) == 125
    assert "Migrated 125 issue(s) to project demo." in capsys.readouterr().out


def _write_proposal_file(
    path: Path,
    *,
    origin: str,
    title: str,
    body: str,
    reply_to: str = "",
    created: str = "2026-06-01",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            f"origin: {origin}\n"
            "to: demo\n"
            f"reply_to: {reply_to}\n"
            f"created: {created}\n"
            f"title: {title}\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
        newline="\n",
    )


def _proposal_payload(origin: str, status: str, title: str) -> dict[str, object]:
    return {
        "origin": origin,
        "reply_to": None,
        "created": "2026-06-01",
        "title": title,
        "body": f"{title} body.",
        "status": status,
        "adopted_issue_number": None,
    }
