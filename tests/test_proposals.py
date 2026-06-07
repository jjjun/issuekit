import json
import subprocess
from pathlib import Path

from issuekit import cli
from issuekit.commands.propose import _git_commit
from issuekit.proposals import (
    Proposal,
    adopt_proposal,
    discard_proposal,
    list_incoming,
    write_proposal,
)
from issuekit.commands.complete import complete_issue
from issuekit.refs import add_ref
from issuekit.workflow import claim_next

from tests.issue_helpers import issue_text, make_issue_tree, write_indexes, write_issue


def test_write_and_list_incoming_proposal(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    proposal = Proposal(
        origin="source#42@abc123",
        to="target",
        reply_to="",
        created="2026-06-03",
        title="Short Proposal",
        body="## Context\n\nBody.",
    )

    path = write_proposal(issues_dir, proposal)
    duplicate_path = write_proposal(issues_dir, proposal)
    incoming = list_incoming(issues_dir)

    data = path.read_bytes()
    assert path == duplicate_path
    assert len(list((issues_dir / "incoming").glob("*.md"))) == 1
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in data
    assert incoming[0].origin == "source#42@abc123"
    assert incoming[0].title == "Short Proposal"


def test_incoming_is_ignored_by_validate_and_claim(tmp_path: Path, monkeypatch) -> None:
    issues_dir = make_issue_tree(tmp_path)
    write_proposal(
        issues_dir,
        Proposal(
            origin="source#42@abc123",
            to="target",
            reply_to="",
            created="2026-06-03",
            title="Incoming Only",
            body="## Suggested Change\n\nDo not claim this.",
        ),
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["validate"]) == 0
    claimed = claim_next(issues_dir, "codex")

    assert claimed is not None
    assert claimed.id == 1
    assert claimed.title == "First"


def test_adopt_proposal_creates_active_issue_and_moves_source(tmp_path: Path) -> None:
    issues_dir = make_issue_tree(tmp_path)
    path = write_proposal(
        issues_dir,
        Proposal(
            origin="source#42@abc123",
            to="target",
            reply_to="",
            created="2026-06-03",
            title="Adopt Me",
            body="## Suggested Change\n\nImplement this locally.",
        ),
    )

    active_path = adopt_proposal(issues_dir, path.name, priority="low")

    content = active_path.read_text(encoding="utf-8")
    assert active_path.name == "003_adopt_me.md"
    assert "priority: low" in content
    assert "origin: source#42@abc123" in content
    assert "- Origin: `source#42@abc123`" in content
    assert not path.exists()
    assert (issues_dir / "incoming" / "adopted" / path.name).exists()


def test_discard_proposal_moves_source(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    path = write_proposal(
        issues_dir,
        Proposal(
            origin="source#42@abc123",
            to="target",
            reply_to="",
            created="2026-06-03",
            title="Discard Me",
            body="## Suggested Change\n\nNo.",
        ),
    )

    discarded = discard_proposal(issues_dir, path.name)

    assert discarded == issues_dir / "incoming" / "discarded" / path.name
    assert discarded.exists()
    assert not path.exists()


def test_reply_proposal_uses_adopted_issue_origin(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    target_issues = target / "docs" / "issues"
    write_issue(
        target_issues / "active" / "001_adopted.md",
        issue_text(1, "Adopted").replace(
            "title: Adopted\n",
            "origin: source#42@abc123\ntitle: Adopted\n",
        ),
    )
    source_issues = source / "docs" / "issues"
    source_issues.mkdir(parents=True)
    monkeypatch.chdir(target)
    assert cli.main(["add-ref", "source", "--path", str(source)]) == 0
    assert cli.main(["propose", "--reply", "1", "--title", "Implemented Reply"]) == 0

    proposals = list_incoming(source_issues)

    assert len(proposals) == 1
    assert proposals[0].to == "source"
    assert proposals[0].reply_to == "source#42@abc123"
    assert proposals[0].origin.startswith("target#1@")


def test_reply_after_adopt_claim_and_complete_preserves_origin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_issues = source / "docs" / "issues"
    target_issues = target / "docs" / "issues"
    source_issues.mkdir(parents=True)
    proposal_path = write_proposal(
        target_issues,
        Proposal(
            origin="source#42@abc123",
            to="target",
            reply_to="",
            created="2026-06-03",
            title="Adopted Reply Flow",
            body="## Suggested Change\n\nImplement this.",
        ),
    )
    adopted_path = adopt_proposal(target_issues, proposal_path.name)

    claimed = claim_next(target_issues, "codex")
    completed = complete_issue(
        target_issues,
        1,
        summary="Implemented.",
        verification="pytest",
        force=True,
    )

    assert claimed is not None
    assert claimed.frontmatter.data["origin"] == "source#42@abc123"
    assert completed.frontmatter.data["origin"] == "source#42@abc123"

    monkeypatch.chdir(target)
    assert cli.main(["add-ref", "source", "--path", str(source)]) == 0
    assert cli.main(["propose", "--reply", "1", "--title", "Implemented Reply"]) == 0

    proposals = list_incoming(source_issues)

    assert adopted_path.name == "001_adopted_reply_flow.md"
    assert len(proposals) == 1
    assert proposals[0].reply_to == "source#42@abc123"
    assert proposals[0].origin.startswith("target#1@")


def test_cli_propose_json_outputs_structured_payload(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source_issues = source / "docs" / "issues"
    target_issues = target / "docs" / "issues"
    write_issue(source_issues / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(source_issues)
    write_indexes(target_issues)
    add_ref("target", target, source)

    monkeypatch.chdir(source)
    exit_code = cli.main(
        [
            "propose",
            "--to",
            "target",
            "--title",
            "CLI Proposal",
            "--body",
            "## Suggested Change\n\nDo this.",
            "--from-issue",
            "1",
            "--json",
        ]
    )
    sent = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert set(sent) == {"file", "origin", "to", "reply_to", "created", "title", "path"}
    assert sent["to"] == "target"
    assert sent["origin"].startswith("source#1@")
    assert sent["title"] == "CLI Proposal"
    assert sent["path"].endswith(sent["file"])
    assert (target_issues / "incoming" / sent["file"]).exists()


def test_cli_propose_without_json_keeps_human_output(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_issue(source / "docs" / "issues" / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(source / "docs" / "issues")
    write_indexes(target / "docs" / "issues")
    add_ref("target", target, source)

    monkeypatch.chdir(source)
    exit_code = cli.main(
        ["propose", "--to", "target", "--title", "CLI Proposal", "--body", "## Suggested Change\n\nDo this."]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.startswith("Wrote proposal:")


def test_cli_adopt_json_outputs_issue_payload(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = make_issue_tree(tmp_path)
    path = write_proposal(
        issues_dir,
        Proposal(
            origin="source#42@abc123",
            to="target",
            reply_to="",
            created="2026-06-03",
            title="Adopt Me",
            body="## Suggested Change\n\nImplement this locally.",
        ),
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["adopt", path.name, "--priority", "low", "--json"])
    adopted = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert set(adopted) == {"id", "title", "status", "assignee", "stage", "implementer", "file", "body"}
    assert adopted["title"] == "Adopt Me"
    assert "Implement this locally." in adopted["body"]


def test_git_commit_timeout_returns_unknown(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_commit(tmp_path) == "unknown"


def test_git_commit_redirects_stdin(tmp_path: Path, monkeypatch) -> None:
    # Regression: inside the issuekit-mcp stdio server an inherited stdin pipe
    # makes `git` block until the timeout, so stdin must be redirected away.
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_commit(tmp_path) == "abc123"
    assert captured["stdin"] == subprocess.DEVNULL
