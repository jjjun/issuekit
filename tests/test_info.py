import json
from pathlib import Path

from issuekit import cli

from tests.issue_helpers import make_issue_tree


def test_info_json_shape(tmp_path: Path, monkeypatch) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info", "--json"])

    assert exit_code == 0


def test_info_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["counts"] == {"active": 1, "completed": 1, "total": 2}
    assert payload["nextIssueId"] == 3
    assert payload["indexes"]["ok"] is True
    assert payload["activeIssues"][0]["file"] == "active/001_first.md"


def test_info_text_reports_index_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    (issues_dir / "indexes" / "active.md").write_text("stale\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Indexes: needs regeneration" in captured.out
    assert "Stale: active.md" in captured.out


def test_info_counts_non_utf8_issue_file_without_crashing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = make_issue_tree(tmp_path)
    (issues_dir / "active" / "003_cp932.md").write_bytes(b"# Issue #3: \x83e\x83X\x83g\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["counts"] == {"active": 2, "completed": 1, "total": 3}
    assert payload["activeIssues"][1]["file"] == "active/003_cp932.md"
    assert payload["activeIssues"][1]["title"] == "cp932"
