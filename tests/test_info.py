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
