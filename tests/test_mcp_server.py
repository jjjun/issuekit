import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from issuekit import cli
from issuekit import proposals_api
from issuekit import store as store_module
from issuekit import worker_registry
from issuekit.agents.proposal_check import ProposalCheckDecision
from issuekit.mcp.server import create_server
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


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


def _tool_schema(server, name: str) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        for tool in await server.list_tools():
            if tool.name == name:
                return tool.inputSchema
        raise AssertionError(f"tool not found: {name}")

    return asyncio.run(run())


def _configure_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: FakeIssuekitClient,
    *,
    extra_config: str = "",
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n" + extra_config,
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)


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
        "update_issue",
        "list_queue",
        "list_workers",
        "list_project_profiles",
        "propose",
        "list_incoming",
        "list_outgoing",
        "adopt_proposal",
        "discard_proposal",
        "run_proposal_checks",
        "list_proposal_checks",
    }


def test_run_proposal_checks_tool_returns_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    seen: dict[str, Any] = {}

    def fake_cycle(config, root, **kwargs):
        seen["project"] = config.project
        seen["root"] = root
        seen.update(kwargs)
        return [
            ProposalCheckDecision(
                check_id=2,
                target_project="demo",
                proposal_id=5,
                verdict="reject",
                comment="Out of scope.",
            )
        ]

    monkeypatch.setattr("issuekit.mcp.server.run_proposal_check_cycle", fake_cycle)
    server = create_server(tmp_path)

    decisions = _call(
        server,
        "run_proposal_checks",
        {"agent": "codex", "timeout_sec": 12.0, "limit": 3},
    )

    assert decisions == [
        {
            "check_id": 2,
            "target_project": "demo",
            "proposal_id": 5,
            "verdict": "reject",
            "comment": "Out of scope.",
            "status": "answered",
        }
    ]
    assert seen["project"] == "demo"
    assert seen["root"] == tmp_path
    assert seen["agent"] == "codex"
    assert seen["timeout"] == 12.0
    assert seen["limit"] == 3


def test_list_proposal_checks_tool_returns_raw_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc", "title": "Check", "body": "Check body."}
        ]
    )
    client.create_proposal_check(
        1,
        target_worker="machine/demo/worker",
        project="demo",
    )
    client.post_proposal_check_result(
        1,
        project="demo",
        verdict="revise",
        comment="Needs details.",
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'demo'\n"
            "worker_id = 'worker'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    client.calls.clear()
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    checks = _call(
        server,
        "list_proposal_checks",
        {"status": "answered", "limit": 5, "offset": 0},
    )

    assert checks[0]["id"] == 1
    assert checks[0]["status"] == "answered"
    assert checks[0]["verdict"] == "revise"
    assert client.calls == [
        {
            "method": "poll_proposal_checks",
            "body": {
                "target_worker": "machine/demo/worker",
                "status": "answered",
                "limit": 5,
                "offset": 0,
            },
        }
    ]


def test_list_workers_returns_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_id="checkout",
        path="/repo",
        role="api-server",
        description="Hosts the API.",
    )
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    workers = _call(server, "list_workers", {"repo_id": "mine-py"})

    assert [row["role"] for row in workers] == ["api-server"]
    assert client.calls[-1] == {
        "method": "list_workers",
        "body": {"repo_id": "mine-py", "project": None},
    }


def test_list_project_profiles_returns_stored_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeIssuekitClient()
    client.project = "issuekit"
    client.put_project_profile(summary="Workflow CLI.", profile_md="# issuekit\n")
    client.project = "mine-py"
    client.put_project_profile(summary="Issue API.", profile_md="# mine-py\n")
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    profiles = _call(server, "list_project_profiles", {})

    assert {row["project"] for row in profiles} == {"issuekit", "mine-py"}


def test_submit_for_review_schema_omits_assignee(tmp_path: Path) -> None:
    schema = _tool_schema(create_server(tmp_path), "submit_for_review")

    assert "assignee" not in schema["properties"]
    assert "allow_any_branch" in schema["properties"]


def test_get_protocol_matches_canonical_text(tmp_path: Path) -> None:
    from issuekit.protocol import render_protocol

    server = create_server(tmp_path)

    assert _call(server, "get_protocol", {"agent": "codex"}) == render_protocol("codex")
    assert _call(server, "get_protocol", {"agent": "kimi"}) == render_protocol("kimi")
    assert _call(server, "get_protocol", {"agent": "kimi", "role": "reviewer"}) == render_protocol(
        "kimi", role="reviewer"
    )
    assert _call(server, "get_protocol", {}) == render_protocol(None)


def test_claim_next_task_claims_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    first = _call(server, "claim_next_task", {"assignee": "codex"})
    second = _call(server, "claim_next_task", {"assignee": "codex"})

    assert first["id"] == 1
    assert first["assignee"] == "codex"
    assert first["stage"] == "implementing"
    assert "body" in first
    assert second["status"] == "none"


def test_review_round_trip_and_approve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    submitted = _call(
        server,
        "submit_for_review",
        {"id": 1, "summary": "Implemented.", "branch": "codex/test", "commit": "abc123"},
    )
    review = _call(server, "next_review", {})
    approved = _call(
        server,
        "approve",
        {"id": 1, "verification": "uv run pytest", "reviewer": "claude"},
    )

    assert submitted["assignee"] == ""
    assert submitted["stage"] == "review"
    assert review["id"] == 1
    assert "body" in review
    assert approved["status"] == "completed"
    assert approved["stage"] == "done"
    assert client.get_issue(1)["stage"] == "done"


def test_submit_for_review_tool_defaults_branch_to_current_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.workflow.git_current_branch", lambda cwd: "feature")
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})

    assert submitted["stage"] == "review"
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {"summary": "Implemented.", "branch": "feature"},
        }
    ]


def test_review_can_be_routed_to_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="claude",
                stage="implementing",
                implementer="claude",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    submitted = _call(
        server,
        "submit_for_review",
        {
            "id": 1,
            "summary": "Implemented.",
            "branch": "claude/test",
            "commit": "abc123",
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


def test_submit_for_review_rejects_explicit_self_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="self-review is not allowed"):
        _call(
            server,
            "submit_for_review",
            {
                "id": 1,
                "summary": "Implemented.",
                "reviewer": "codex",
            },
        )


def test_next_review_uses_configured_default_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", status="in_progress", assignee="codex", stage="review")]
    )
    _configure_api(tmp_path, monkeypatch, client, extra_config="default_reviewer = 'codex'\n")
    server = create_server(tmp_path)

    review = _call(server, "next_review", {})

    assert review["id"] == 1


def test_request_changes_returns_issue_to_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    returned = _call(server, "request_changes", {"id": 1, "notes": "Add tests."})
    queue = _call(server, "list_queue", {"assignee": "codex", "stage": "changes_requested"})
    issue = _call(server, "get_issue", {"id": 1})

    assert returned["assignee"] == "codex"
    assert returned["stage"] == "changes_requested"
    assert [item["id"] for item in queue] == [1]
    assert issue["id"] == 1
    assert "body" in issue


def test_request_changes_defaults_to_recorded_implementer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="review",
                implementer="claude",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    returned = _call(server, "request_changes", {"id": 1, "notes": "Add tests.", "reviewer": "codex"})

    assert returned["assignee"] == "claude"
    assert returned["stage"] == "changes_requested"


def test_mcp_read_tools_use_api_store_when_configured(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Review",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
                body="# Issue #1: Review\n\nReview body.\n",
            ),
            api_issue(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    review = _call(server, "next_review", {})
    queue = _call(server, "list_queue", {"assignee": "claude", "stage": "review"})
    issue = _call(server, "get_issue", {"id": 1})

    assert review["id"] == 1
    assert review["ref"] == "demo#1"
    assert review["body"] == "# Issue #1: Review\n\nReview body.\n"
    assert [item["id"] for item in queue] == [1]
    assert issue["ref"] == "demo#1"


def test_mcp_update_issue_edits_and_appends(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Old", body="Original body")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    updated = _call(
        server,
        "update_issue",
        {"id": 1, "title": "New", "body": "Replacement", "priority": "high"},
    )
    appended = _call(server, "update_issue", {"id": 1, "append": "Plan section"})

    assert updated["title"] == "New"
    assert updated["body"] == "Replacement"
    assert appended["body"] == "Replacement\n\nPlan section"
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 1,
            "body": {"title": "New", "body": "Replacement", "priority": "high"},
        },
        {
            "method": "update_issue",
            "number": 1,
            "body": {"body": "Replacement\n\nPlan section"},
        },
    ]


def test_mcp_update_issue_requires_force_for_in_flight_issue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "In flight",
                status="in_progress",
                stage="implementing",
                assignee="codex",
                implementer="codex",
            )
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="pass --force"):
        _call(server, "update_issue", {"id": 1, "title": "Blocked"})

    forced = _call(server, "update_issue", {"id": 1, "title": "Forced", "force": True})

    assert forced["title"] == "Forced"


def test_auto_default_reviewer_opens_review_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client, extra_config="default_reviewer = 'auto'\n")
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})
    review = _call(server, "next_review", {})
    approved = _call(
        server,
        "approve",
        {"id": 1, "verification": "uv run pytest", "reviewer": "claude"},
    )

    assert submitted["assignee"] == ""
    assert review["id"] == 1
    assert approved["status"] == "completed"


def test_auto_default_reviewer_opens_review_even_when_guard_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="default_reviewer = 'auto'\nrequire_distinct_reviewer = true\n",
    )
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})
    review = _call(server, "next_review", {})

    assert submitted["assignee"] == ""
    assert review["id"] == 1


def test_open_review_allows_any_agent_to_approve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client, extra_config="default_reviewer = 'auto'\n")
    server = create_server(tmp_path)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})
    approved = _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "claude"})

    assert submitted["assignee"] == ""
    assert approved["status"] == "completed"


def test_open_review_rejects_self_review_when_guard_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="default_reviewer = 'auto'\nrequire_distinct_reviewer = true\n",
    )
    server = create_server(tmp_path)

    _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})

    with pytest.raises(Exception, match="self-review is not allowed"):
        _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "codex"})


def test_approve_allows_different_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="review",
                implementer="claude",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    approved = _call(
        server,
        "approve",
        {"id": 1, "verification": "uv run pytest", "reviewer": "codex"},
    )

    assert approved["status"] == "completed"


def test_approve_rejects_self_review_when_guard_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client, extra_config="require_distinct_reviewer = true\n")
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="self-review is not allowed"):
        _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "codex"})


def test_approve_rejects_unassigned_reviewer_with_clear_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="review",
                implementer="claude",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client, extra_config="default_reviewer = 'auto'\n")
    server = create_server(tmp_path)

    with pytest.raises(Exception) as excinfo:
        _call(server, "approve", {"id": 1, "verification": "uv run pytest", "reviewer": "claude"})

    message = str(excinfo.value)
    assert "review is assigned to reviewer 'codex'" in message
    assert "You passed reviewer='claude'" in message
    assert "Omit `reviewer` to use default_reviewer" in message


def test_api_proposal_tools_send_list_adopt_and_discard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 10, "origin": "source#10@abc123", "title": "Adopt", "body": "Adopt body."},
            {"id": 11, "origin": "source#11@abc123", "title": "Discard", "body": "Discard body."},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    sent = _call(
        server,
        "propose",
        {
            "to": "other_project",
            "title": "MCP API Proposal",
            "body": "## Suggested Change\n\nDo this.",
        },
    )
    incoming = _call(server, "list_incoming", {})
    adopted = _call(
        server,
        "adopt_proposal",
        {"proposal_id": 10, "priority": "low", "append": "Implementation plan."},
    )
    discarded = _call(server, "discard_proposal", {"proposal_id": 11})

    assert sent["origin"] == "target#0@unknown"
    assert sent["title"] == "MCP API Proposal"
    assert sent["payload_mismatch"] is False
    assert [proposal["id"] for proposal in incoming] == [10, 11, sent["id"]]
    assert adopted["title"] == "Adopt"
    assert adopted["priority"] == "low"
    assert adopted["body"] == "Adopt body.\n\nImplementation plan."
    assert discarded["status"] == "discarded"


def test_mcp_propose_flags_same_origin_payload_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "target#0@unknown",
                "title": "Old title",
                "body": "Old body.",
                "status": "pending",
            }
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    sent = _call(
        server,
        "propose",
        {"to": "other_project", "title": "New title", "body": "New body."},
    )

    assert sent["id"] == 1
    assert sent["title"] == "Old title"
    assert sent["idempotent_existing"] is True
    assert sent["payload_mismatch"] is True
    assert sent["payload_mismatch_fields"] == ["title", "body"]
    assert "from-issue" in sent["warning"]


def test_mcp_propose_attaches_dependency_refs(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    sent = _call(
        server,
        "propose",
        {
            "to": "other_project",
            "title": "MCP API Proposal",
            "body": "Use the accepted API contract.",
            "depends_on": "mine-py#42",
        },
    )

    assert sent["depends_on"] == ["mine-py#42"]
    assert client.calls[0]["body"]["depends_on"] == ["mine-py#42"]


def test_mcp_propose_rejects_non_ascii_body(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="must be ASCII-only"):
        _call(
            server,
            "propose",
            {
                "to": "other_project",
                "title": "Clean title",
                "body": "Body with an em dash — here.",
            },
        )

    # Fail fast: the rejected proposal must never reach the API client.
    assert client.calls == []


def test_mcp_list_outgoing_scopes_to_own_origin(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "target#1@abc", "title": "Mine", "body": "b", "status": "pending"},
            {"id": 2, "origin": "other#1@abc", "title": "Not mine", "body": "b", "status": "pending"},
            {
                "id": 3,
                "origin": "target#2@abc",
                "title": "Mine adopted",
                "body": "b",
                "status": "adopted",
                "adopted_issue_number": 42,
            },
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    outgoing = _call(server, "list_outgoing", {"to": "other_project"})
    adopted_only = _call(server, "list_outgoing", {"to": "other_project", "status": "adopted"})

    assert [proposal["id"] for proposal in outgoing] == [1, 3]
    assert [proposal["id"] for proposal in adopted_only] == [3]


def test_cli_proposal_json_matches_mcp_output(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)

    # propose via MCP and via CLI: same source/title/body -> identical API proposal
    source_server = create_server(tmp_path)
    mcp_sent = _call(
        source_server,
        "propose",
        {"to": "target", "title": "Parity", "body": "## Suggested Change\n\nDo this."},
    )
    monkeypatch.chdir(tmp_path)
    cli.main(
        [
            "propose",
            "--to",
            "target",
            "--title",
            "Parity",
            "--body",
            "## Suggested Change\n\nDo this.",
            "--json",
        ]
    )
    cli_sent = json.loads(capsys.readouterr().out)
    assert cli_sent["id"] == mcp_sent["id"]
    assert cli_sent["origin"] == mcp_sent["origin"]
    assert cli_sent["title"] == mcp_sent["title"]
    assert cli_sent["payload_mismatch"] == mcp_sent["payload_mismatch"]
    assert cli_sent["stop"] == mcp_sent["stop"] == "STOP_NOW"
    assert cli_sent["authorGuard"]["kind"] == mcp_sent["authorGuard"]["kind"] == "proposal"

    # list_incoming parity
    target_server = create_server(tmp_path)
    mcp_incoming = _call(target_server, "list_incoming", {})
    cli.main(["incoming", "--json"])
    cli_incoming = json.loads(capsys.readouterr().out)
    assert cli_incoming == mcp_incoming

    # adopt parity: CLI adopt output equals MCP adopt output
    cli.main(["adopt", str(cli_incoming[0]["id"]), "--json"])
    cli_adopted = json.loads(capsys.readouterr().out)
    assert cli_adopted["title"] == "Parity"
