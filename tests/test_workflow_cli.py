from pathlib import Path

from issuekit import cli

from tests.issue_helpers import issue_text, write_indexes, write_issue


def assert_single_frontmatter_body_gap(content: str) -> None:
    assert "\n---\n\n# Issue" in content
    assert "\n---\n\n\n" not in content


def test_claim_command_claims_issue_and_updates_indexes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--assignee", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=1" in captured.out
    assert "assignee=codex stage=implementing" in captured.out
    assert "in_progress" in (issues_dir / "indexes" / "active.md").read_text(encoding="utf-8")
    assert_single_frontmatter_body_gap(
        (issues_dir / "active" / "001_first.md").read_text(encoding="utf-8")
    )
    assert cli.main(["validate"]) == 0


def test_handoff_commands_round_trip_issue(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        [
            "submit-review",
            "1",
            "--summary",
            "Implemented.",
            "--branch",
            "codex/test",
            "--commit",
            "abc123",
        ]
    )
    request_exit = cli.main(["request-changes", "1", "--notes", "Add tests."])

    captured = capsys.readouterr()
    content = (issues_dir / "active" / "001_first.md").read_text(encoding="utf-8")
    assert submit_exit == 0
    assert request_exit == 0
    assert "assignee=claude stage=review" in captured.out
    assert "assignee=codex stage=changes_requested" in captured.out
    assert_single_frontmatter_body_gap(content)
    assert "## Handoff" in content
    assert "## Review Feedback" in content
    assert cli.main(["validate"]) == 0


def test_handoff_commands_accept_reviewer(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="claude",
            stage="implementing",
            implementer="claude",
        ),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        [
            "submit-review",
            "1",
            "--summary",
            "Implemented.",
            "--assignee",
            "claude",
            "--reviewer",
            "codex",
        ]
    )
    request_exit = cli.main(["request-changes", "1", "--notes", "Add tests.", "--reviewer", "codex"])

    captured = capsys.readouterr()
    assert submit_exit == 0
    assert request_exit == 0
    assert "assignee=codex stage=review" in captured.out
    assert "assignee=claude stage=changes_requested" in captured.out


def test_queue_command_lists_matching_issues(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_review.md",
        issue_text(1, "Review", status="in_progress", assignee="claude", stage="review"),
    )
    write_issue(
        issues_dir / "active" / "002_work.md",
        issue_text(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["queue", "--assignee", "claude", "--stage", "review"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=1" in captured.out
    assert "id=2" not in captured.out


def test_submit_review_rejects_non_ascii_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["submit-review", "1", "--summary", "\u3042"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err


def test_handoff_commands_reject_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        ["submit-review", "bad-id", "--summary", "Implemented."],
    )
    request_exit = cli.main(["request-changes", "bad-id", "--notes", "Add tests."])

    assert submit_exit == 1
    assert request_exit == 1
    out = capsys.readouterr()
    assert "Invalid issue id: bad-id" in out.err
