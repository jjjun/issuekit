import json
import pytest
import subprocess
from pathlib import Path

from issuekit import cli
from issuekit.commands import propose as propose_command
from issuekit.commands.propose import _git_commit
from issuekit.proposals import (
    Proposal,
    adopt_proposal,
    discard_proposal,
    ProposalError,
    list_incoming,
    _resolve_proposal_path,
    slugify,
    write_proposal,
)
from issuekit.testing import FakeIssuekitClient
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


def test_slugify_cap_and_default_from_proposals() -> None:
    assert slugify("Draft: New Proposal!!!") == "draft_new_proposal"
    assert slugify("###") == "proposal"
    assert slugify("A" * 80) == ("a" * 64)


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


def test_resolve_proposal_path_accepts_incoming_filename(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    path = write_proposal(
        issues_dir,
        Proposal(
            origin="source#42@abc123",
            to="target",
            reply_to="",
            created="2026-06-03",
            title="Safe Path",
            body="## Suggested Change\n\nKeep this local.",
        ),
    )

    assert _resolve_proposal_path(issues_dir, path.name) == path


def test_resolve_proposal_path_rejects_absolute_path(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    absolute_path = tmp_path / "outside.md"
    absolute_path.write_text(
        "---\norigin: source#42@abc123\nto: target\nreply_to:\ncreated: 2026-06-03\ntitle: Outside\n---\n\n# Proposal: Outside\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ProposalError, match="must be an incoming-relative file"):
        adopt_proposal(issues_dir, str(absolute_path))


def test_resolve_proposal_path_rejects_directory_traversal(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    escaped = issues_dir.parent / "outside.md"
    escaped.parent.mkdir(parents=True, exist_ok=True)
    escaped.write_text(
        "---\norigin: source#42@abc123\nto: target\nreply_to:\ncreated: 2026-06-03\ntitle: Escaped\n---\n\n# Proposal: Escaped\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ProposalError, match="escapes incoming directory"):
        adopt_proposal(issues_dir, "../outside.md")


def test_resolve_proposal_path_rejects_missing_file(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"

    with pytest.raises(ProposalError, match="Proposal file not found"):
        adopt_proposal(issues_dir, "does-not-exist.md")


def test_resolve_proposal_path_rejects_non_file(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    directory = issues_dir / "incoming" / "directory"
    directory.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ProposalError, match="not a file"):
        discard_proposal(issues_dir, "directory")


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
    assert set(adopted) == {
        "id",
        "title",
        "status",
        "assignee",
        "stage",
        "implementer",
        "author",
        "file",
        "body",
    }
    assert adopted["title"] == "Adopt Me"
    assert "Implement this locally." in adopted["body"]


def test_api_cli_propose_posts_expected_body_and_dedupes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    created_projects: list[str] = []
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )

    def fake_client(*args, **kwargs):
        created_projects.append(kwargs["project"])
        return client

    monkeypatch.setattr(propose_command, "IssuekitClient", fake_client)
    monkeypatch.chdir(tmp_path)

    argv = [
        "propose",
        "--to",
        "target",
        "--title",
        "API Proposal",
        "--body",
        "## Suggested Change\n\nDo this.",
        "--json",
    ]
    assert cli.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(argv) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["id"] == second["id"]
    assert first["origin"] == "source#0@unknown"
    assert first["title"] == "API Proposal"
    assert client.calls[0] == {
        "method": "create_proposal",
        "body": {
            "origin": "source#0@unknown",
            "title": "API Proposal",
            "body": "## Suggested Change\n\nDo this.",
        },
    }
    assert created_projects == ["target", "target"]
    assert not (tmp_path / "docs" / "issues" / "incoming").exists()


def test_api_cli_incoming_lists_pending_large_inbox(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": proposal_id,
                "origin": f"source#{proposal_id}@abc123",
                "title": f"Proposal {proposal_id}",
                "body": "Body",
                "status": "pending",
            }
            for proposal_id in range(1, 121)
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(propose_command, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["incoming", "--json"]) == 0
    incoming = json.loads(capsys.readouterr().out)

    assert len(incoming) == 120
    assert incoming[0]["id"] == 1
    assert incoming[-1]["id"] == 120


def test_api_cli_adopt_and_discard_use_proposal_ids(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc123", "title": "Adopt", "body": "Adopt body."},
            {"id": 2, "origin": "source#2@abc123", "title": "Discard", "body": "Discard body."},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(propose_command, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "1", "--priority", "high", "--json"]) == 0
    adopted = json.loads(capsys.readouterr().out)
    assert cli.main(["discard", "2", "--json"]) == 0
    discarded = json.loads(capsys.readouterr().out)

    assert adopted["title"] == "Adopt"
    assert adopted["priority"] == "high"
    assert client.get_proposal(1)["status"] == "adopted"
    assert discarded["status"] == "discarded"
    assert client.get_proposal(2)["status"] == "discarded"


def test_api_cli_adopt_requires_integer_id(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "proposal.md"]) == 1

    assert "Proposal id must be an integer" in capsys.readouterr().err


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
