import json
import subprocess
from pathlib import Path

from issuekit import cli
from issuekit import proposals_api
from issuekit.config import IssuekitConfig, TriagePolicy
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
    assert first["payload_mismatch"] is False
    assert second["payload_mismatch"] is False
    assert "idempotent_existing" not in second
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


def test_api_cli_propose_can_mark_blocking(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Blocking API Proposal",
                "--body",
                "Needed by source.",
                "--blocking",
                "--json",
            ]
        )
        == 0
    )
    sent = json.loads(capsys.readouterr().out)

    assert sent["blocking"] is True
    assert client.calls[0] == {
        "method": "create_proposal",
        "body": {
            "origin": "source#0@unknown",
            "title": "Blocking API Proposal",
            "body": "Needed by source.",
            "blocking": True,
        },
    }


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


def test_api_cli_propose_same_origin_payload_mismatch_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#0@unknown",
                "title": "Old title",
                "body": "Old body.",
                "status": "pending",
            }
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    argv = ["propose", "--to", "target", "--title", "New title", "--body", "New body."]
    assert cli.main([*argv, "--json"]) == 1
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert output["id"] == 1
    assert output["title"] == "Old title"
    assert output["idempotent_existing"] is True
    assert output["payload_mismatch"] is True
    assert output["payload_mismatch_fields"] == ["title", "body"]
    assert "--from-issue" in captured.err
    assert "source#0@unknown" in captured.err

    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert "Sent proposal" not in captured.out
    assert "--from-issue" in captured.err


def test_api_cli_outgoing_lists_own_proposals(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc", "title": "Mine pending", "body": "b", "status": "pending"},
            {"id": 2, "origin": "other#1@abc", "title": "Not mine", "body": "b", "status": "pending"},
            {
                "id": 3,
                "origin": "source#2@abc",
                "title": "Mine adopted",
                "body": "b",
                "status": "adopted",
                "adopted_issue_number": 42,
            },
        ]
    )
    created_projects: list[str] = []

    def fake_client(*args, **kwargs):
        created_projects.append(kwargs["project"])
        return client

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["outgoing", "--to", "target", "--json"]) == 0
    outgoing = json.loads(capsys.readouterr().out)
    assert [proposal["id"] for proposal in outgoing] == [1, 3]
    assert set(created_projects) == {"target"}

    assert cli.main(["outgoing", "--to", "target", "--status", "adopted", "--json"]) == 0
    adopted = json.loads(capsys.readouterr().out)
    assert [proposal["id"] for proposal in adopted] == [3]

    assert cli.main(["outgoing", "--to", "target", "--id", "3", "--json"]) == 0
    single = json.loads(capsys.readouterr().out)
    assert [proposal["id"] for proposal in single] == [3]

    assert cli.main(["outgoing", "--to", "target"]) == 0
    text = capsys.readouterr().out
    assert "target#42" in text
    assert "Mine pending" in text
    assert "Not mine" not in text


def test_api_cli_outgoing_rejects_foreign_and_invalid_lookups(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 2, "origin": "other#1@abc", "title": "Not mine", "body": "b", "status": "pending"},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["outgoing", "--to", "target", "--id", "2"]) == 1
    assert "was not sent by source" in capsys.readouterr().err

    assert cli.main(["outgoing", "--to", "target", "--status", "bogus"]) == 1
    assert "Invalid proposal status" in capsys.readouterr().err


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
    append_file = tmp_path / "plan.md"
    append_file.write_text("## Implementation Plan\n\nDo this.\n", encoding="utf-8", newline="\n")

    assert cli.main(["adopt", "1", "--priority", "high", "--append-file", str(append_file), "--json"]) == 0
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
    assert adopted["issue"]["body"] == "Adopt body.\n\n## Implementation Plan\n\nDo this."
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["body"] == "Adopt body.\n\n## Implementation Plan\n\nDo this."
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


def test_auto_adopt_incoming_proposals_filters_policy_and_caps(monkeypatch) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#1@abc123",
                "title": "Blocking",
                "body": "Blocking body.",
                "blocking": True,
            },
            {
                "id": 2,
                "origin": "source#2@abc123",
                "title": "Not blocking",
                "body": "Body.",
                "blocking": False,
            },
            {
                "id": 3,
                "origin": "other#1@abc123",
                "title": "Foreign",
                "body": "Body.",
                "blocking": True,
            },
            {
                "id": 4,
                "origin": "source#3@abc123",
                "title": "Second blocking",
                "body": "Body.",
                "blocking": True,
            },
        ]
    )
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="target",
        triage=TriagePolicy(
            trusted_origins=("source",),
            default_priority="high",
            require_blocking=True,
            max_adoptions_per_cycle=1,
        ),
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)

    adopted = proposals_api.auto_adopt_incoming_proposals(config)

    assert [item["proposal_id"] for item in adopted] == ["1"]
    assert adopted[0]["auto_adopted"] is True
    assert adopted[0]["blocking"] is True
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["priority"] == "high"
    assert client.get_issue(1)["origin_proposal_id"] == "1"
    assert client.get_proposal(2)["status"] == "pending"
    assert client.get_proposal(3)["status"] == "pending"
    assert client.get_proposal(4)["status"] == "pending"


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
