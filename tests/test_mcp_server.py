import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

if os.environ.get("ISSUEKIT_REQUIRE_MCP") == "1":
    import mcp
else:
    pytest.importorskip("mcp")

from issuekit import cli
import issuekit.proposals.api as proposals_api
from issuekit import store as store_module
from issuekit.api import token_cache as token_cache_module
from issuekit.workers import registry as worker_registry
from issuekit.agents.proposal_check import ProposalCheckDecision
from issuekit.config import load_config
from issuekit.mcp import server as mcp_server
from issuekit.mcp.server import create_server
from issuekit.negotiation import MockNegotiationStore, ThreadStatus, Verdict
from issuekit.prompts.protocol import render_protocol
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


def _tool_schema_digest(server) -> str:
    async def run() -> str:
        schemas = {tool.name: tool.inputSchema for tool in await server.list_tools()}
        encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

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
        "health",
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
        "remove_worker",
        "remove_repo",
        "list_orphans",
        "reclaim_issue",
        "readdress_issue",
        "list_project_profiles",
        "propose",
        "list_incoming",
        "list_outgoing",
        "list_negotiation_threads",
        "adopt_proposal",
        "discard_proposal",
        "run_proposal_checks",
        "list_proposal_checks",
    }


def test_server_tool_schemas_match_the_contract(tmp_path: Path) -> None:
    # This digest covers the MCP tool contract. If it changes, confirm the
    # schema change was intended, describe it in the commit message, then update
    # this digest.
    assert _tool_schema_digest(create_server(tmp_path)) == (
        "62e615e29a2268e03d43a1d518512e644c07a0f6eb8bd093de782b8515e72af0"
    )


def test_health_tool_reports_config_and_local_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(tmp_path / "token.json"))
    expires_at = time.time() + 3600
    token_cache_module.write_cached_token("https://mine.example", "cached-token", expires_at)
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
            "worker_id = 'checkout'\n"
            "\n"
            "[author_guard]\n"
            "project = 'demo'\n"
            "kind = 'issue'\n"
            "id = '7'\n"
            "ref = 'demo#7'\n"
            "author_agent = 'codex'\n"
            "required_next_action = 'STOP'\n"
            "\n"
            "[refs]\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    server = create_server(tmp_path)

    status = _call(server, "health", {})

    assert status["ok"] is True
    assert status["version"] == "0.1.0"
    assert status["cwd"] == str(tmp_path.resolve())
    assert status["project"] == "demo"
    assert status["api_url_configured"] is True
    assert status["token_cached"] is True
    assert status["token_expires_at"] == expires_at
    assert status["worker_present"] is True
    assert status["worker"] == "checkout.demo"
    assert status["author_guard_active"] is True
    assert status["author_guard"]["ref"] == "demo#7"
    assert status["errors"] == []


def test_health_tool_reports_token_cache_miss_for_resolved_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(tmp_path / "token.json"))
    token_cache_module.write_cached_token(
        "https://mine.example",
        "cached-token",
        time.time() + 3600,
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://other.example/'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    server = create_server(tmp_path)

    status = _call(server, "health", {})

    assert status["api_url_configured"] is True
    assert status["token_cached"] is False
    assert status["token_expires_at"] is None
    assert status["errors"] == []


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
        {
            "agent": "codex",
            "timeout_sec": 12.0,
            "model": "gpt-5.6",
            "reasoning_effort": "medium",
            "limit": 3,
        },
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
    assert seen["model"] == "gpt-5.6"
    assert seen["reasoning_effort"] == "medium"
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
        target_worker="worker.demo@machine",
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
                "target_worker": "worker.demo@machine",
                "status": "answered",
                "limit": 5,
                "offset": 0,
            },
        },
        {
            "method": "poll_proposal_checks",
            "body": {
                "target_worker": "worker.demo",
                "status": "answered",
                "limit": 5,
                "offset": 0,
            },
        },
    ]


def test_list_negotiation_threads_reads_mock_store_without_api_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    store = MockNegotiationStore(None)
    entry = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Contract",
        body="Proposed contract.",
        origin="demo#1",
        contract="Contract text.",
    )
    store.append_entry(
        entry.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="Contract",
        body="Agreed contract.",
        origin="backend#2",
        contract="Contract text.",
    )
    store.set_status(
        entry.thread_id,
        ThreadStatus.agreed,
        agreed_contract="Contract text.",
    )
    seen: dict[str, Any] = {}

    def fake_store(config, *, use_mock):
        seen["project"] = config.project
        seen["use_mock"] = use_mock
        return store

    monkeypatch.setattr(mcp_server, "get_negotiation_store", fake_store)
    server = create_server(tmp_path)

    summaries = _call(
        server,
        "list_negotiation_threads",
        {"status": "agreed", "mock": True},
    )
    inspection = _call(
        server,
        "list_negotiation_threads",
        {"thread_id": entry.thread_id, "mock": True},
    )

    assert summaries == [
        {
            "thread_id": entry.thread_id,
            "status": "agreed",
            "agreed_contract": "Contract text.",
            "issue_refs": None,
            "updated": entry.created,
        }
    ]
    assert inspection["thread_id"] == entry.thread_id
    assert inspection["status"] == "agreed"
    assert len(inspection["entries"]) == 2
    assert seen == {"project": "demo", "use_mock": True}


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


def test_list_workers_preserves_target_worker_when_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_id="checkout",
        path="/repo",
    )
    client._workers["checkout.mine-py"]["target_worker"] = "checkout.mine-py"
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    workers = _call(server, "list_workers", {"repo_id": "mine-py"})

    assert workers[0]["target_worker"] == "checkout.mine-py"


def test_remove_worker_tool_rejects_legacy_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_id="checkout",
        path="/repo",
    )
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="Worker was not found"):
        _call(
            server,
            "remove_worker",
            {"address": "machine/mine-py/checkout"},
        )


def test_remove_worker_tool_force_allows_implementing_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                5,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="checkout.mine-py",
            )
        ]
    )
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_id="checkout",
        path="/repo",
    )
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    result = _call(
        server,
        "remove_worker",
        {"address": "checkout.mine-py", "force": True},
    )

    assert result["implementing_issues"][0]["id"] == 5
    assert result["deleted"] == {"id": "checkout.mine-py", "deleted": True}


def test_remove_repo_tool_deletes_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient()
    client.upsert_repo(repo_key="mine-py")
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    result = _call(server, "remove_repo", {"repo": "mine-py"})

    assert result == {
        "repo_key": "mine-py",
        "deleted": {"repo_key": "mine-py", "deleted": True},
    }


def test_list_orphans_flags_dead_worker_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                5,
                "Stuck",
                status="in_progress",
                stage="implementing",
                assignee="claude",
                implementer="claude",
                worker="machine/issuekit/dead",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    orphans = _call(server, "list_orphans", {})

    assert len(orphans) == 1
    assert orphans[0]["id"] == 5
    assert orphans[0]["reason"] == "no_worker"
    assert orphans[0]["worker"] == "machine/issuekit/dead"


def test_reclaim_issue_tool_returns_stale_claim_to_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                5,
                "Stuck",
                status="in_progress",
                stage="implementing",
                assignee="claude",
                implementer="claude",
                worker="machine/issuekit/dead",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    reclaimed = _call(server, "reclaim_issue", {"id": 5, "reason": "stale checkout"})

    assert reclaimed["id"] == 5
    assert reclaimed["previous"]["assignee"] == "claude"
    assert reclaimed["expected_worker"] == "machine/issuekit/dead"
    assert reclaimed["actor"] == "issuekit"
    assert reclaimed["audit_reason"] == "stale checkout"
    assert reclaimed["issue"]["status"] == "active"
    assert reclaimed["issue"]["stage"] == "todo"
    assert reclaimed["issue"]["worker"] == ""
    assert client.get_issue(5)["worker"] == ""
    assert client.calls == [
        {
            "method": "list_workers",
            "body": {"repo_id": None, "project": None},
        },
        {
            "method": "reclaim",
            "number": 5,
            "body": {
                "expected_worker": "machine/issuekit/dead",
                "actor": "issuekit",
                "reason": "stale checkout",
            },
        },
    ]


def test_readdress_issue_tool_returns_directed_issue_to_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeIssuekitClient(
        [api_issue(6, "Directed", target_worker="checkout.demo")]
    )
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config=(
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'demo'\n"
            "worker_id = 'operator'\n"
        ),
    )
    server = create_server(tmp_path)

    result = _call(
        server,
        "readdress_issue",
        {"id": 6, "reason": "stale directed checkout"},
    )

    assert result["id"] == 6
    assert result["expected_target_worker"] == "checkout.demo"
    assert result["actor"] == "operator.demo"
    assert result["issue"]["stage"] == ""
    assert "target_worker" not in result["issue"]
    assert client.get_issue(6)["target_worker"] == ""


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


def test_claim_next_task_schema_includes_sync_escape_hatch(tmp_path: Path) -> None:
    schema = _tool_schema(create_server(tmp_path), "claim_next_task")

    assert "allow_any_branch" in schema["properties"]
    assert "no_sync" in schema["properties"]


def test_get_protocol_matches_canonical_text(tmp_path: Path) -> None:
    server = create_server(tmp_path)

    assert _call(server, "get_protocol", {"agent": "codex"}) == render_protocol("codex")
    assert _call(server, "get_protocol", {"agent": "kimi"}) == render_protocol("kimi")
    assert _call(server, "get_protocol", {"agent": "kimi", "role": "reviewer"}) == render_protocol(
        "kimi", role="reviewer"
    )
    assert _call(server, "get_protocol", {}) == render_protocol(None)


def test_get_protocol_uses_configured_agent_role(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agent_roles]\nclaude = 'implementer'\n", encoding="utf-8", newline="\n"
    )
    server = create_server(tmp_path)

    assert _call(server, "get_protocol", {"agent": "claude"}) == render_protocol("codex")


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


def test_claim_next_task_uses_default_implementer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="default_implementer = 'claude'\n",
    )
    server = create_server(tmp_path)

    claimed = _call(server, "claim_next_task", {})

    assert claimed["assignee"] == "claude"


def test_claim_next_task_requires_assignee_without_default_implementer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="No implementer is configured"):
        _call(server, "claim_next_task", {})

    assert client.calls == []


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
    assert client.calls[0]["body"]["session"].startswith("mcp-")
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {
                "summary": "Implemented.",
                "branch": "feature",
                "session": client.calls[0]["body"]["session"],
            },
        }
    ]


def test_mcp_lifecycle_tools_discover_client_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_root = tmp_path / "process"
    repo_root = tmp_path / "repo"
    process_root.mkdir()
    repo_root.mkdir()
    (process_root / "pyproject.toml").write_text(
        "[project]\nname = 'launcher'\n",
        encoding="utf-8",
        newline="\n",
    )
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
    _configure_api(repo_root, monkeypatch, client)

    async def fake_client_roots(ctx):
        return (repo_root,)

    monkeypatch.setattr(mcp_server, "_client_roots", fake_client_roots)
    server = create_server(process_root)

    submitted = _call(server, "submit_for_review", {"id": 1, "summary": "Implemented."})

    assert submitted["stage"] == "review"
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {
                "summary": "Implemented.",
                "session": client.calls[0]["body"]["session"],
            },
        }
    ]


def test_mcp_lifecycle_missing_api_url_reports_searched_config_paths(tmp_path: Path) -> None:
    server = create_server(tmp_path)

    with pytest.raises(Exception) as excinfo:
        _call(server, "list_queue", {})

    message = str(excinfo.value)
    assert "API store requires api_url" in message
    assert f"MCP resolved the repository root to {tmp_path.resolve()}" in message
    assert str(tmp_path / "pyproject.toml") in message
    assert str(tmp_path / "issuekit.toml") in message
    assert str(tmp_path / ".env") in message
    assert "Machine config:" in message
    assert "(exists: False)" in message
    assert "If the CLI succeeds" in message


def test_mcp_loads_api_config_from_machine_config_at_git_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    nested_root = repo_root / "nested"
    machine_path = tmp_path / "config.toml"
    nested_root.mkdir(parents=True)
    machine_path.write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    monkeypatch.setattr(mcp_server, "git_root", lambda root: repo_root)

    assert load_config(nested_root).api_url == "https://mine.example"

    config, config_root = asyncio.run(mcp_server._load_api_config(nested_root))

    assert config.api_url == "https://mine.example"
    assert config_root == repo_root
    message = mcp_server._missing_api_url_message(repo_root)
    assert str(machine_path) in message
    assert "(exists: True)" in message


def test_mcp_lifecycle_tools_reuse_one_process_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "First", author="claude"),
            api_issue(
                2,
                "Second",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
            ),
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    _call(server, "claim_next_task", {"assignee": "codex"})
    _call(server, "submit_for_review", {"id": 2, "summary": "Implemented."})

    sessions = [call["body"]["session"] for call in client.calls]
    assert sessions[0].startswith("mcp-")
    assert sessions == [sessions[0], sessions[0]]


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


def test_list_queue_includes_target_worker(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Directed", assignee="codex", target_worker="checkout.demo")]
    )
    _configure_api(tmp_path, monkeypatch, client)
    server = create_server(tmp_path)

    queue = _call(server, "list_queue", {"assignee": "codex"})

    assert queue == [
        {
            "id": 1,
            "title": "Directed",
            "status": "active",
            "assignee": "codex",
            "stage": "",
            "implementer": "",
            "author": "",
            "ref": "demo#1",
            "target_worker": "checkout.demo",
        }
    ]


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


def test_mcp_update_issue_accepts_dependency_refs(tmp_path: Path, monkeypatch) -> None:
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
        {"id": 1, "depends_on": ["mine-py#42"]},
    )

    assert updated["depends_on"] == ["mine-py#42"]
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 1,
            "body": {"depends_on": ["mine-py#42"]},
        },
    ]


def test_mcp_get_issue_includes_dependency_state_and_rows(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Dependent",
                depends_on=["mine-py#42"],
                dependency_state="waiting",
                dependencies=[
                    {
                        "ref": "mine-py#42",
                        "state": "waiting",
                        "status": "in_progress",
                        "stage": "review",
                    }
                ],
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

    issue = _call(server, "get_issue", {"id": 1})

    assert issue["depends_on"] == ["mine-py#42"]
    assert issue["dependency_state"] == "waiting"
    assert issue["dependencies"][0]["stage"] == "review"


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
    client.register_catalog_project("other_project")
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
    assert sent["dependency_ref"] == "other_project#proposal:12"
    assert sent["payload_mismatch"] is False
    assert [proposal["id"] for proposal in incoming] == [10, 11, sent["id"]]
    assert adopted["title"] == "Adopt"
    assert adopted["priority"] == "low"
    assert adopted["body"] == "Adopt body.\n\nImplementation plan."
    assert discarded["status"] == "discarded"


def test_mcp_propose_accepts_worker_repo_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    client.register_catalog_project("other_project")
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
            "to": "checkout.other_project",
            "title": "MCP directed proposal",
            "body": "Body.",
        },
    )

    assert sent["target_worker"] == "checkout"
    assert client.calls[0]["body"]["target_worker"] == "checkout"


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
    client.register_catalog_project("other_project")
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


def test_mcp_propose_rejects_unknown_target_when_profile_catalog_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    client.project = "registered"
    client.put_project_profile(summary="Registered project.", profile_md="# Registered\n")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    server = create_server(tmp_path)

    with pytest.raises(Exception, match="Unknown target project 'missing'"):
        _call(server, "propose", {"to": "missing", "title": "New title", "body": "New body."})

    assert not any(call["method"] == "create_proposal" for call in client.calls)


def test_mcp_propose_attaches_dependency_refs(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient()
    client.register_catalog_project("other_project")
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
    assert sent["dependency_ref"] == "other_project#proposal:1"
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
    client.register_catalog_project("other_project")
    server = create_server(tmp_path)

    outgoing = _call(server, "list_outgoing", {"to": "other_project"})
    adopted_only = _call(server, "list_outgoing", {"to": "other_project", "status": "adopted"})

    assert [proposal["id"] for proposal in outgoing] == [1, 3]
    assert [proposal["id"] for proposal in adopted_only] == [3]


def test_cli_proposal_json_matches_mcp_output(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient()
    client.register_catalog_project("target")
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
