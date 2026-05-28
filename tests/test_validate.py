from pathlib import Path

from issuekit import cli

from tests.issue_helpers import issue_text, make_issue_tree, write_indexes, write_issue


def test_validate_clean_tree_passes(tmp_path: Path, monkeypatch, capsys) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 0
    assert "Issue validation passed" in capsys.readouterr().out


def test_validate_duplicate_id_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_issue(issues_dir / "active" / "002_duplicate.md", issue_text(2, "Duplicate"))
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 1
    assert "Issue id 2 is used by" in capsys.readouterr().err


def test_validate_stale_index_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    (issues_dir / "indexes" / "active.md").write_text("stale\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 1
    assert "Generated index is stale" in capsys.readouterr().err


def test_validate_bad_frontmatter_status_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_bad.md", issue_text(1, "Bad", status="bad"))
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 1
    assert "invalid status" in capsys.readouterr().err


def test_validate_non_ascii_over_threshold_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_bad.md",
        issue_text(1, "Bad") + "\nNon ASCII: \u3042\n",
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err
