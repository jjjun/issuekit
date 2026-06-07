import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from issuekit import cli
from issuekit.mcp.server import create_server
from issuekit.refs import add_ref

from tests.issue_helpers import issue_text, write_indexes, write_issue


def _call(server, name: str, arguments: dict[str, Any]) -> Any:
    async def run() -> Any:
        result = await server.call_tool(name, arguments)
        if isinstance(result, tuple):
            content, structured = result
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured if structured is not None else json.loads(content[0].text)
        return json.loads(result[0].text)

    return asyncio.run(run())


def _tool_names(server) -> set[str]:
    async def run() -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    return asyncio.run(run())


def test_importing_cli_does_not_import_mcp() -> None:
    assert cli.main(["--help"]) == 0


def test_server_registers_expected_tools(tmp_path: Path) -> None:
    server = create_server(tmp_path)

    assert _tool_names(server) == {
        "get_protocol",
        "claim_next_task",
        "submit_for_review",
        "next_review",
        "request_changes",
        "approve",
        "get_issue",
        "list_queue",
        "propose",
        "list_incoming",
        "adopt_proposal",
    }


def test_get_protocol_matches_canonical_text(tmp_path: Path) -> None:
    from issuekit.protocol import render_protocol

    server = create_server(tmp_path)

    assert _call(server, "get_protocol", {"agent": "codex"}) == render_protocol("codex")
    assert _call(server, "get_protocol", {"agent": "kimi"}) == render_protocol("kimi")
    assert _call(server, "get_protocol", {"agent": "kimi", "role": "reviewer"}) == render_protocol(
        "kimi", role="reviewer"
    )
    assert _call(server, "get_protocol", {}) == render_protocol(None)


def test_claim_next_task_claims_only_once(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    first = _call(server, "claim_next_task", {"assignee": "codex"})
    second = _call(server, "claim_next_task", {"assignee": "codex"})

    assert first["id"] == 1
    assert first["assignee"] == "codex"
    assert first["stage"] == "implementing"
    assert "body" in first
    assert second["status"] == "none"


def test_review_round_trip_and_approve(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    submitted = _call(
        server,
        "submit_for_review",
        {"id": 1, "summary": "Implemented.", "branch": "codex/test", "commit": "abc123"},
    )
    review = _call(server, "next_review", {})
    approved = _call(server, "approve", {"id": 1, "verification": "uv run pytest"})

    assert submitted["assignee"] == "claude"
    assert submitted["stage"] == "review"
    assert review["id"] == 1
    assert "body" in review
    assert approved["status"] == "completed"
    assert approved["stage"] == "done"
    assert (issues_dir / "completed" / "001_first.md").exists()
    assert "Approved by claude." in (issues_dir / "completed" / "001_first.md").read_text(
        encoding="utf-8"
    )


def test_review_can_be_routed_to_codex(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="claude",
            stage="implementing",
            implementer="claude",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    submitted = _call(
        server,
        "submit_for_review",
        {
            "id": 1,
            "summary": "Implemented.",
            "branch": "claude/test",
            "commit": "abc123",
            "assignee": "claude",
            "reviewer": "codex",
        },
    )
    review = _call(server, "next_review", {"reviewer": "codex"})
    approved = _call(
        server,
        "approve",
        {"id": 1, "verification": "uv run pytest", "reviewer": "codex"},
    )

    assert submitted["assignee"] == "codex"
    assert submitted["stage"] == "review"
    assert review["id"] == 1
    assert approved["status"] == "completed"
    assert "Approved by codex." in (issues_dir / "completed" / "001_first.md").read_text(
        encoding="utf-8"
    )


def test_next_review_uses_configured_default_reviewer(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'codex'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="review"),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    review = _call(server, "next_review", {})

    assert review["id"] == 1


def test_request_changes_returns_issue_to_codex(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="claude",
            stage="review",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    returned = _call(server, "request_changes", {"id": 1, "notes": "Add tests."})
    queue = _call(server, "list_queue", {"assignee": "codex", "stage": "changes_requested"})
    issue = _call(server, "get_issue", {"id": 1})

    assert returned["assignee"] == "codex"
    assert returned["stage"] == "changes_requested"
    assert [item["id"] for item in queue] == [1]
    assert issue["id"] == 1
    assert "body" in issue


def test_request_changes_defaults_to_recorded_implementer(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="review",
            implementer="claude",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    returned = _call(server, "request_changes", {"id": 1, "notes": "Add tests.", "reviewer": "codex"})

    assert returned["assignee"] == "claude"
    assert returned["stage"] == "changes_requested"


def test_auto_default_reviewer_opens_review_pool(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})
    review = _call(server, "next_review", {})
    approved = _call(server, "approve", {"id": 1, "verification": "uv run pytest"})

    assert submitted["assignee"] == ""
    assert review["id"] == 1
    assert approved["status"] == "completed"
    assert "Approved by codex." in (issues_dir / "completed" / "001_first.md").read_text(
        encoding="utf-8"
    )


def test_auto_default_reviewer_opens_review_even_when_guard_is_required(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\nrequire_distinct_reviewer = true\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})
    review = _call(server, "next_review", {})

    assert submitted["assignee"] == ""
    assert review["id"] == 1


def test_open_review_allows_any_agent_to_approve(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})
    approved = _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "claude"})

    assert submitted["assignee"] == ""
    assert approved["status"] == "completed"
    assert "Approved by claude." in (issues_dir / "completed" / "001_first.md").read_text(
        encoding="utf-8"
    )


def test_open_review_rejects_self_review_when_guard_is_required(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\nrequire_distinct_reviewer = true\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})

    with pytest.raises(Exception, match="self-review is not allowed"):
        _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "codex"})


def test_approve_allows_self_review_by_default(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="review",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    approved = _call(
        server,
        "approve",
        {"id": 1, "verification": "uv run pytest", "reviewer": "codex"},
    )

    assert approved["status"] == "completed"


def test_approve_rejects_self_review_when_guard_is_required(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_distinct_reviewer = true\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="review",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="self-review is not allowed"):
        _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "codex"})


def test_approve_rejects_unassigned_reviewer_with_clear_message(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="review",
            implementer="claude",
        ),
    )
    write_indexes(issues_dir)
    server = create_server(tmp_path)

    with pytest.raises(Exception) as excinfo:
        _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "claude"})

    message = str(excinfo.value)
    assert "review is assigned to reviewer 'codex'" in message
    assert "You passed reviewer='claude'" in message
    assert "Omit `reviewer` to use default_reviewer" in message


def test_proposal_tools_send_list_and_adopt(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source_issues = source / "docs" / "issues"
    target_issues = target / "docs" / "issues"
    write_issue(source_issues / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(source_issues)
    write_indexes(target_issues)
    add_ref("target", target, source)

    source_server = create_server(source)
    sent = _call(
        source_server,
        "propose",
        {
            "to": "target",
            "title": "MCP Proposal",
            "body": "## Suggested Change\n\nDo this.",
            "from_issue": "1",
        },
    )
    target_server = create_server(target)
    incoming = _call(target_server, "list_incoming", {})
    adopted = _call(target_server, "adopt_proposal", {"proposal_file": incoming[0]["file"]})

    assert sent["origin"].startswith("source#1@")
    assert incoming[0]["title"] == "MCP Proposal"
    assert adopted["id"] == 1
    assert adopted["title"] == "MCP Proposal"


def test_cli_proposal_json_matches_mcp_output(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source_issues = source / "docs" / "issues"
    target_issues = target / "docs" / "issues"
    write_issue(source_issues / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(source_issues)
    write_indexes(target_issues)
    add_ref("target", target, source)

    # propose via MCP and via CLI: same source/title/body -> identical proposal_dict
    source_server = create_server(source)
    mcp_sent = _call(
        source_server,
        "propose",
        {"to": "target", "title": "Parity", "body": "## Suggested Change\n\nDo this.", "from_issue": "1"},
    )
    monkeypatch.chdir(source)
    cli.main(
        [
            "propose",
            "--to",
            "target",
            "--title",
            "Parity",
            "--body",
            "## Suggested Change\n\nDo this.",
            "--from-issue",
            "1",
            "--json",
        ]
    )
    cli_sent = json.loads(capsys.readouterr().out)
    assert cli_sent == mcp_sent

    # list_incoming parity
    target_server = create_server(target)
    mcp_incoming = _call(target_server, "list_incoming", {})
    monkeypatch.chdir(target)
    cli.main(["incoming", "--json"])
    cli_incoming = json.loads(capsys.readouterr().out)
    assert cli_incoming == mcp_incoming

    # adopt parity: CLI adopt output equals MCP adopt output keys/values
    cli.main(["adopt", cli_incoming[0]["file"], "--json"])
    cli_adopted = json.loads(capsys.readouterr().out)
    assert set(cli_adopted) == {
        "id",
        "title",
        "status",
        "assignee",
        "stage",
        "implementer",
        "file",
        "body",
    }
    assert cli_adopted["title"] == "Parity"
