import json
from pathlib import Path

from issuekit import cli
from issuekit.proposals import Proposal, write_proposal

from tests.issue_helpers import issue_text, make_issue_tree, write_issue, write_indexes


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
    assert payload["activeIssues"][0]["stage"] is None
    assert payload["incomingProposals"] == []


def test_info_json_lists_incoming_proposals(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_proposal(
        issues_dir,
        Proposal(
            origin="mine-js-monorepo#0@f8b6c5b3",
            to="issuekit",
            reply_to="",
            created="2026-06-03",
            title="Show Pending Proposal",
            body="## Suggested Change\n\nSurface this in info.",
        ),
    )
    monkeypatch.chdir(tmp_path)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["counts"] == {"active": 1, "completed": 1, "total": 2}
    assert payload["incomingProposals"] == [
        {
            "origin": "mine-js-monorepo#0@f8b6c5b3",
            "title": "Show Pending Proposal",
            "created": "2026-06-03",
            "file": "incoming/mine_js_monorepo__0__show_pending_proposal.md",
        }
    ]


def test_info_text_lists_incoming_proposals(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_proposal(
        issues_dir,
        Proposal(
            origin="mine-js-monorepo#0@f8b6c5b3",
            to="issuekit",
            reply_to="",
            created="2026-06-03",
            title="Show Pending Proposal",
            body="## Suggested Change\n\nSurface this in info.",
        ),
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Incoming proposals: 1" in captured.out
    assert "Incoming proposals\n- mine-js-monorepo#0@f8b6c5b3: Show Pending Proposal" in captured.out


def test_info_ignores_triaged_incoming_proposals(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_proposal(
        issues_dir,
        Proposal(
            origin="mine-js-monorepo#0@f8b6c5b3",
            to="issuekit",
            reply_to="",
            created="2026-06-03",
            title="Show Pending Proposal",
            body="## Suggested Change\n\nSurface this in info.",
        ),
    )
    adopted_dir = issues_dir / "incoming" / "adopted"
    adopted_dir.mkdir()
    (issues_dir / "incoming" / "mine_js_monorepo__0__show_pending_proposal.md").replace(
        adopted_dir / "mine_js_monorepo__0__show_pending_proposal.md"
    )
    monkeypatch.chdir(tmp_path)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["incomingProposals"] == []


def test_info_text_reports_index_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    (issues_dir / "indexes" / "active.md").write_text("stale\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Indexes: needs regeneration" in captured.out
    assert "Stale: active.md" in captured.out
    assert "Incoming proposals: 0" in captured.out
    assert "\nIncoming proposals\n" not in captured.out


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


def test_info_json_includes_stage_when_present(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_issue(
        issues_dir / "active" / "003_review.md",
        issue_text(3, "Review", status="in_progress", stage="review"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    review_issue = next(i for i in payload["activeIssues"] if i["id"] == 3)
    assert review_issue["status"] == "in_progress"
    assert review_issue["stage"] == "review"


def test_info_text_renders_stage_when_present(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_issue(
        issues_dir / "active" / "003_review.md",
        issue_text(3, "Review", status="in_progress", stage="review"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[in_progress, stage=review]" in captured.out


def test_info_text_renders_status_only_when_no_stage(tmp_path: Path, monkeypatch, capsys) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["info"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[active]" in captured.out
    assert "stage=" not in captured.out
