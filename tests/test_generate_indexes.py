from pathlib import Path

from issuekit import cli

from tests.issue_helpers import issue_text, write_issue


def test_generate_indexes_writes_expected_files_and_removes_stale(tmp_path: Path, monkeypatch) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    write_issue(
        issues_dir / "completed" / "002_done.md",
        issue_text(2, "Done", status="completed", completed="2026-01-02"),
    )
    stale = issues_dir / "indexes" / "stale.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["generate-indexes"])

    assert exit_code == 0
    assert not stale.exists()
    assert (issues_dir / "indexes" / "active.md").exists()
    assert (issues_dir / "indexes" / "completed-recent.md").exists()
    assert (issues_dir / "indexes" / "completed-001-099.md").exists()


def test_generate_indexes_writes_without_bom_or_crlf(tmp_path: Path, monkeypatch) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    monkeypatch.chdir(tmp_path)

    cli.main(["generate-indexes"])

    content = (issues_dir / "indexes" / "active.md").read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in content
