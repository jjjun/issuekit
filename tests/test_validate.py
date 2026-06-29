from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue, issue_text, make_issue_tree, write_indexes, write_issue


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


def test_validate_non_utf8_issue_file_fails_without_traceback(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = make_issue_tree(tmp_path)
    bad_issue = issues_dir / "active" / "003_cp932.md"
    bad_issue.write_bytes(b"# Issue #3: \x83e\x83X\x83g\n")
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Issue file is not valid UTF-8: active/003_cp932.md" in captured.err
    assert "Traceback" not in captured.err


def test_validate_api_mode_checks_connectivity_and_shape(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "API validation passed (1 issues)." in captured.out


def test_validate_api_mode_fails_on_malformed_issue_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class MalformedClient:
        def list_issues(self, **kwargs):
            return [{"id": 1, "status": "active"}]

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: MalformedClient())
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "missing required field" in captured.err
