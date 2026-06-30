import json
import subprocess
from pathlib import Path

from issuekit import cli
from issuekit import proposals_api
from issuekit.proposals_api import _git_commit
from issuekit.proposals import ProposalError, origin_destination
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


def test_origin_destination_uses_project_segment() -> None:
    assert origin_destination("source#42@abc123") == "source"


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

    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)
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


def test_api_cli_propose_from_issue_reads_api_store(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class NoListClient(FakeIssuekitClient):
        def list_all_issues(self, *args, **kwargs):
            raise AssertionError("build_proposal should fetch the source issue directly")

    client = NoListClient(
        issues=[
            api_issue(
                7,
                "Source Issue",
                body="# Issue #7: Source Issue\n\n## Suggested Change\n\nFrom API.",
            )
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("issuekit.store.IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["propose", "--to", "target", "--from-issue", "7", "--json"]) == 0
    sent = json.loads(capsys.readouterr().out)

    assert sent["origin"] == "source#7@unknown"
    assert sent["title"] == "Source Issue"
    assert sent["body"] == "# Issue #7: Source Issue\n\n## Suggested Change\n\nFrom API."


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
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
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
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "1", "--priority", "high", "--json"]) == 0
    adopted = json.loads(capsys.readouterr().out)
    assert cli.main(["discard", "2", "--json"]) == 0
    discarded = json.loads(capsys.readouterr().out)

    assert adopted["title"] == "Adopt"
    assert adopted["priority"] == "high"
    assert adopted["api_result"] == "created_issue"
    assert adopted["created_api_issue"] is True
    assert adopted["proposal_id"] == "1"
    assert adopted["issue_id"] == 1
    assert adopted["issue_ref"] == "target#1"
    assert adopted["next_command"] == "issuekit claim --id 1 --assignee <agent>"
    assert adopted["issue"]["title"] == "Adopt"
    assert client.get_proposal(1)["status"] == "adopted"
    assert discarded["status"] == "discarded"
    assert client.get_proposal(2)["status"] == "discarded"


def test_api_cli_adopt_normal_output_includes_next_step(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc123", "title": "Adopt", "body": "Adopt body."},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "1", "--priority", "high"]) == 0

    out = capsys.readouterr().out
    assert "Adopted proposal #1 as API issue #1 (target#1)." in out
    assert "Next: issuekit claim --id 1 --assignee <agent>" in out


def test_api_cli_adopt_requires_integer_id(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "proposal.md"]) == 1

    assert "Proposal id must be an integer" in capsys.readouterr().err


def test_proposal_commands_require_api_url(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["incoming"]) == 1

    assert "Proposal commands require api_url" in capsys.readouterr().err


def test_invalid_origin_destination_raises() -> None:
    try:
        origin_destination("not-an-origin")
    except ProposalError as exc:
        assert "Invalid proposal origin" in str(exc)
    else:
        raise AssertionError("expected ProposalError")


def test_git_commit_timeout_returns_unknown(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_commit(tmp_path) == "unknown"


def test_git_commit_redirects_stdin(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_commit(tmp_path) == "abc123"
    assert captured["stdin"] == subprocess.DEVNULL
