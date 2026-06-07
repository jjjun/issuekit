from pathlib import Path

from issuekit import cli

from tests.issue_helpers import issue_text, make_issue_tree, write_issue


def assert_single_frontmatter_body_gap(content: str) -> None:
    assert "\n---\n\n# Issue" in content
    assert "\n---\n\n\n" not in content


def test_complete_moves_issue_updates_frontmatter_and_regenerates_indexes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_review_before_complete = false\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

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
    completed = issues_dir / "completed" / "001_first.md"
    assert exit_code == 0
    assert "Completed issue #1" in captured.out
    assert not (issues_dir / "active" / "001_first.md").exists()
    assert completed.exists()
    content = completed.read_text(encoding="utf-8")
    assert "status: completed" in content
    assert "stage: done" in content
    assert "assignee:" not in content
    assert_single_frontmatter_body_gap(content)
    assert "completed:" in content
    assert "- Implemented the command." in content
    assert "- Verification: `uv run pytest`" in content
    assert (issues_dir / "indexes" / "active.md").exists()
    assert cli.main(["validate"]) == 0


def test_complete_missing_issue_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "999"])

    assert exit_code == 1
    assert "Active issue #999 was not found" in capsys.readouterr().err


def test_complete_rejects_non_ascii_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "1", "--summary", "\u3042"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err


def test_complete_writes_without_bom_or_crlf(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_review_before_complete = false\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    monkeypatch.chdir(tmp_path)

    cli.main(["complete", "1"])

    content = (issues_dir / "completed" / "001_first.md").read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in content


def test_complete_normalizes_accumulated_frontmatter_body_gap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_review_before_complete = false\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First").replace("---\n\n# Issue", "---\n\n\n\n# Issue"),
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "1", "--summary", "Approved."])

    content = (issues_dir / "completed" / "001_first.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert_single_frontmatter_body_gap(content)
    assert "- Approved." in content


def test_complete_rejects_non_utf8_issue_without_modifying_it(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    bad_issue = issues_dir / "active" / "001_cp932.md"
    bad_issue.parent.mkdir(parents=True, exist_ok=True)
    raw_content = b"# Issue #1: \x83e\x83X\x83g\n"
    bad_issue.write_bytes(raw_content)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "1"])

    assert exit_code == 1
    assert "Active issue #1 is not valid UTF-8: active/001_cp932.md" in capsys.readouterr().err
    assert bad_issue.read_bytes() == raw_content
    assert not (issues_dir / "completed" / "001_cp932.md").exists()


def test_complete_rejects_non_review_stage_by_default(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First", stage="implementing"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "1", "--summary", "Done."])

    assert exit_code == 1
    assert "must reach the review stage before completion" in capsys.readouterr().err
    assert not (issues_dir / "completed" / "001_first.md").exists()


def test_complete_allows_force_bypass(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First", stage="implementing"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "1", "--force", "--summary", "Done."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Completed issue #1" in captured.out
    assert (issues_dir / "completed" / "001_first.md").exists()


def test_complete_allows_review_stage_by_default(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First", stage="review"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["complete", "1", "--summary", "Approved."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Completed issue #1" in captured.out
    assert (issues_dir / "completed" / "001_first.md").exists()
