"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context, FastMCP

from issuekit import __version__
from issuekit.agents.proposal_check import list_worker_proposal_checks
from issuekit.api.token_cache import read_cached_token
from issuekit.commands.approve import approve_issue
from issuekit.commands.dispatch import dispatch_issue as command_dispatch_issue
from issuekit.commands.edit import edit_issue
from issuekit.commands.proposal_check_request import request_proposal_check
from issuekit.commands.readdress import readdress_result_dict
from issuekit.commands.reclaim import reclaim_result_dict
from issuekit.config import IssuekitConfig, load_config, resolve_machine_config_path
from issuekit.config.local import LocalConfigError, load_toml, read_local_config
from issuekit.core import issue_dict, worker_display_from_row
from issuekit.gitutil import git_root
from issuekit.guards.author import STOP_SENTINEL, create_author_guard, guard_dict
from issuekit.issues.orphans import (
    DEFAULT_STALE_AFTER_SEC,
    list_stale_claims,
    stale_claim_dict,
)
from issuekit.issues.session import new_session_token
from issuekit.negotiation import NegotiationThreadSummary, ThreadStatus, get_negotiation_store
from issuekit.negotiation.engine import inspect_thread
from issuekit.prompts.protocol import render_protocol, render_server_instructions
from issuekit.proposals.api import (
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    list_outgoing_proposals,
    proposal_id_arg,
    send_proposal,
)
from issuekit.store import get_store
from issuekit.workers.registry import list_api_workers, remove_api_repo, remove_api_worker
from issuekit.workflow import (
    WorkflowError,
    claim_next,
    find_for,
    resolve_implementer,
)
from issuekit.workflow import (
    next_review as workflow_next_review,
)
from issuekit.workflow import (
    readdress_issue as workflow_readdress_issue,
)
from issuekit.workflow import (
    reclaim_issue as workflow_reclaim_issue,
)
from issuekit.workflow import (
    request_changes as workflow_request_changes,
)
from issuekit.workflow import (
    submit_for_review as workflow_submit_for_review,
)

MCP_SESSION = new_session_token("mcp")


def create_server(cwd: Path | str | None = None) -> FastMCP:
    server = FastMCP("issuekit", instructions=render_server_instructions())
    root = Path.cwd() if cwd is None else Path(cwd)

    @server.tool(
        description=(
            "Read-only MCP server health and configuration status. Safe to call "
            "before workflow operations."
        )
    )
    async def health(ctx: Context) -> dict[str, Any]:
        return await _health_status(root, ctx)

    @server.tool(description="Read the current issuekit handoff protocol.")
    def get_protocol(agent: str | None = None, role: str | None = None) -> str:
        config = load_config(root)
        return render_protocol(agent, role=role, agent_roles=config.agent_roles)

    @server.tool(
        description=(
            "Implementer protocol step 1: claim the next task, then implement and call "
            "submit_for_review."
        )
    )
    async def claim_next_task(
        assignee: str | None = None,
        priority: str | None = None,
        allow_author_session: bool = False,
        allow_any_branch: bool = False,
        no_sync: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, config_root, store):
            resolved_assignee = resolve_implementer(assignee, config)
            if resolved_assignee is None:
                raise WorkflowError(
                    "No implementer is configured. Pass assignee, set default_implementer, "
                    "or configure exactly one enabled assignee."
                )
            issue = claim_next(
                resolved_assignee,
                priority=priority,
                config=config,
                store=store,
                cwd=config_root,
                allow_author_guard_override=allow_author_session,
                allow_any_branch=allow_any_branch,
                no_sync=no_sync,
                session=MCP_SESSION,
            )
        if issue is None:
            return {"status": "none", "assignee": resolved_assignee}
        return issue_dict(issue, include_body=True)

    @server.tool(
        description=(
            "Implementation protocol step 2: submit an implemented task for reviewer "
            "handoff with summary and optional branch/commit metadata."
        )
    )
    async def submit_for_review(
        id: int,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
        allow_author_session: bool = False,
        allow_any_branch: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, config_root, store):
            issue = workflow_submit_for_review(
                id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
                config=config,
                store=store,
                cwd=config_root,
                allow_author_guard_override=allow_author_session,
                allow_any_branch=allow_any_branch,
                session=MCP_SESSION,
            )
        return issue_dict(issue)

    @server.tool(
        description=(
            "Reviewer protocol step 1: fetch the next issue waiting for the reviewer, "
            "then call approve or request_changes."
        )
    )
    async def next_review(
        reviewer: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, _config_root, store):
            issue = workflow_next_review(reviewer, config=config, store=store)
        if issue is None:
            return {
                "status": "none",
                "assignee": reviewer or config.default_reviewer,
                "stage": "review",
            }
        return issue_dict(issue, include_body=True)

    @server.tool(
        description="Reviewer protocol decision: return a review issue to its implementer with notes."
    )
    async def request_changes(
        id: int,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, _config_root, store):
            issue = workflow_request_changes(
                id,
                notes=notes,
                reviewer=reviewer,
                assignee=assignee,
                config=config,
                store=store,
                session=MCP_SESSION,
            )
        return issue_dict(issue)

    @server.tool(
        description="Reviewer protocol decision: approve a reviewed issue and move it to completed."
    )
    async def approve(
        id: int,
        verification: str,
        reviewer: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, _config_root, store):
            issue = approve_issue(
                id,
                verification=verification,
                reviewer=reviewer,
                config=config,
                store=store,
                session=MCP_SESSION,
            )
        return issue_dict(issue)

    @server.tool(description="Read one active or completed issue by id.")
    async def get_issue(id: int, ctx: Context | None = None) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, _config_root, store):
            issue = store.get_issue(id)
        if issue is None:
            return {"status": "none", "id": id}
        return issue_dict(issue, include_body=True)

    @server.tool(description="Edit an API-backed issue title, body, appended text, or priority.")
    async def update_issue(
        id: int,
        title: str | None = None,
        body: str | None = None,
        append: str | None = None,
        priority: str | None = None,
        depends_on: list[str] | str | None = None,
        force: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        if body is not None and append is not None:
            raise ValueError("body and append are mutually exclusive.")
        async with _api_store(root, ctx) as (config, _config_root, store):
            issue = edit_issue(
                id,
                title=title,
                body=body,
                append=append,
                priority=priority,
                depends_on=depends_on,
                force=force,
                config=config,
                store=store,
            )
        return issue_dict(issue, include_body=True)

    @server.tool(description="List active queue entries, optionally filtered by assignee and stage.")
    async def list_queue(
        assignee: str | None = None,
        stage: str | None = None,
        with_body: bool = False,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        async with _api_store(root, ctx) as (config, _config_root, store):
            return [
                issue_dict(issue, include_body=with_body)
                for issue in find_for(assignee, stage=stage, config=config, store=store)
            ]

    @server.tool(
        description=(
            "List registered workers and their repo-level roles across issuekit "
            "checkouts, optionally filtered by repo_id and project."
        )
    )
    async def list_workers(
        repo_id: str | None = None,
        project: str | None = None,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        async with _api_config(root, ctx) as (config, _config_root):
            return list_api_workers(config, repo_id=repo_id, project=project)

    @server.tool(
        description=(
            "Remove a registered worker by worker.repo or worker.repo@machine id. "
            "Refuses workers that hold implementing issues unless force is true."
        )
    )
    async def remove_worker(
        address: str,
        force: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            result = remove_api_worker(config, address, force=force)
        return {
            "worker": result.worker,
            "display": worker_display_from_row(result.worker),
            "deleted": result.deleted,
            "implementing_issues": [
                issue_dict(issue) | {"worker": issue.worker}
                for issue in result.implementing_issues
            ],
        }

    @server.tool(
        description=(
            "Remove a registered repo catalog entry. The API refuses repos that "
            "still have worker, issue, or proposal references."
        )
    )
    async def remove_repo(
        repo: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            result = remove_api_repo(config, repo)
        return {"repo_key": result.repo_key, "deleted": result.deleted}

    @server.tool(
        description=(
            "List implementing issues whose claiming worker is gone or has "
            "stopped heartbeating (orphaned or stale claims that the pull pool "
            "will not re-offer)."
        )
    )
    async def list_orphans(
        stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        async with _api_config(root, ctx) as (config, _config_root):
            claims = list_stale_claims(config, stale_after_sec=stale_after_sec)
        return [stale_claim_dict(claim) for claim in claims]

    @server.tool(
        description=(
            "Return an orphaned or stale implementing claim to the implement pool. "
            "By default this refuses claims not listed by list_orphans; pass force "
            "only for human emergency recovery."
        )
    )
    async def reclaim_issue(
        id: int,
        force: bool = False,
        stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
        reason: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            result = workflow_reclaim_issue(
                id,
                force=force,
                stale_after_sec=stale_after_sec,
                reason=reason,
                config=config,
            )
        return reclaim_result_dict(result)

    @server.tool(
        description=(
            "Return a directed issue from a specific worker target back to the "
            "repo pool."
        )
    )
    async def readdress_issue(
        id: int,
        reason: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            result = workflow_readdress_issue(
                id,
                reason=reason,
                config=config,
            )
        return readdress_result_dict(result)

    @server.tool(
        description=(
            "Direct an issue to a registered worker; use readdress_issue to return "
            "it to the repo pool."
        )
    )
    async def dispatch_issue(
        id: int,
        target_worker: str,
        assignee: str | None = None,
        stage: str | None = None,
        allow_unregistered_worker: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_store(root, ctx) as (config, _config_root, store):
            issue = command_dispatch_issue(
                id,
                target_worker=target_worker,
                assignee=assignee,
                stage=stage,
                allow_unregistered_worker=allow_unregistered_worker,
                config=config,
                store=store,
            )
        output = issue_dict(issue)
        output["target_worker"] = issue.target_worker
        return output

    @server.tool(
        description=(
            "List stored project capability profiles across projects (the PM "
            "router's input). Requires a backend that supports project profiles."
        )
    )
    async def list_project_profiles(ctx: Context | None = None) -> list[dict[str, Any]]:
        async with _api_config(root, ctx) as (config, _config_root):
            with api_client(config) as client:
                return client.list_project_profiles()

    @server.tool(
        description=(
            "Send a cross-repository proposal from the origin project to the target "
            "project inbox; use this instead of authoring directly in the target repo. "
            "Pass depends_on as project#N, project#issue:N, or project#proposal:N "
            "for upstream dependencies."
        )
    )
    async def propose(
        to: str | None = None,
        title: str | None = None,
        body: str | None = None,
        from_issue: str | None = None,
        reply: str | None = None,
        blocking: bool = False,
        depends_on: str | None = None,
        agent: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, config_root):
            proposal = build_proposal(
                config_root,
                to=to,
                title=title,
                body=body,
                body_file=None,
                from_issue=from_issue,
                reply=reply,
                blocking=blocking,
                depends_on=depends_on,
            )
            sent = send_proposal(config, proposal)
        if sent.get("payload_mismatch"):
            return sent
        guard = create_author_guard(
            config_root,
            config=config,
            kind="proposal",
            item_id=sent.get("id"),
            ref=f"{proposal.to}#{sent.get('id')}",
            target_project=proposal.to,
            author_agent=agent,
            author_session=MCP_SESSION,
        )
        sent = dict(sent)
        sent["authorGuard"] = guard_dict(guard)
        sent["stop"] = STOP_SENTINEL
        return sent

    @server.tool(description="List incoming cross-repository proposals.")
    async def list_incoming(ctx: Context | None = None) -> list[dict[str, Any]]:
        async with _api_config(root, ctx) as (config, _config_root):
            with api_client(config) as client:
                return client.list_proposals(status="pending")

    @server.tool(
        description=(
            "List proposals this project sent to a target project's inbox "
            "(read-only, scoped to proposals this project authored)."
        )
    )
    async def list_outgoing(
        to: str,
        status: str | None = None,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        async with _api_config(root, ctx) as (config, _config_root):
            return list_outgoing_proposals(config, to=to, status=status)

    @server.tool(
        description=(
            "Inspect persisted negotiation threads without launching agents. Pass thread_id "
            "for entries and finalization state, or status to filter the thread list."
        )
    )
    async def list_negotiation_threads(
        thread_id: str | None = None,
        status: str | None = None,
        mock: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any] | list[dict[str, object]]:
        if status not in (None, "negotiating", "agreed", "blocked", "cancelled"):
            raise ValueError(
                "status must be negotiating, agreed, blocked, or cancelled."
            )
        async with _api_config(root, ctx) as (config, _config_root):
            store = get_negotiation_store(config, use_mock=mock)
            if thread_id:
                return inspect_thread(thread_id, store=store).to_dict()
            thread_status = ThreadStatus(status) if status else None
            return [
                _negotiation_thread_summary_dict(summary)
                for summary in store.list_threads(status=thread_status)
            ]

    @server.tool(description="Adopt an incoming proposal as a local active issue.")
    async def adopt_proposal(
        proposal_id: int | None = None,
        proposal_file: str | None = None,
        priority: str = "medium",
        append: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            raw_id = proposal_id if proposal_id is not None else proposal_id_arg(proposal_file or "")
            return adopt_proposal_with_append(
                config,
                raw_id,
                priority=priority,
                append_text=append,
            )

    @server.tool(description="Discard an incoming cross-repository proposal.")
    async def discard_proposal(
        proposal_id: int | None = None,
        proposal_file: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            raw_id = proposal_id if proposal_id is not None else proposal_id_arg(proposal_file or "")
            with api_client(config) as client:
                return client.discard_proposal(int(raw_id))

    @server.tool(
        description=(
            "Request evaluation of a pending proposal by a registered worker "
            "in the target project."
        )
    )
    async def create_proposal_check(
        to: str,
        proposal_id: int,
        worker: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with _api_config(root, ctx) as (config, _config_root):
            return request_proposal_check(
                config,
                to=to,
                proposal_id=proposal_id,
                worker=worker,
            )

    @server.tool(
        description=(
            "List proposal checks addressed to this registered checkout "
            "(read-only; posts nothing and runs no agent)."
        )
    )
    async def list_proposal_checks(
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        if status not in (None, "pending", "answered"):
            raise ValueError("status must be pending or answered.")
        async with _api_config(root, ctx) as (config, _config_root):
            return list_worker_proposal_checks(
                config,
                status=status,
                limit=limit,
                offset=offset,
            )

    return server


def _negotiation_thread_summary_dict(
    summary: NegotiationThreadSummary,
) -> dict[str, object]:
    return {
        "thread_id": summary.thread_id,
        "status": summary.status.value,
        "agreed_contract": summary.agreed_contract,
        "issue_refs": summary.issue_refs.to_dict() if summary.issue_refs else None,
        "source_proposal_ref": summary.source_proposal_ref,
        "updated": summary.updated,
    }


async def _health_status(root: Path, ctx: Context | None = None) -> dict[str, Any]:
    config_root = await _resolve_config_root(root, ctx)
    payload: dict[str, Any] = {
        "ok": True,
        "version": __version__,
        "cwd": str(config_root.resolve()),
        "project": None,
        "api_url_configured": False,
        "token_cached": False,
        "token_expires_at": None,
        "worker_present": False,
        "worker": None,
        "author_guard_active": False,
        "author_guard": None,
        "errors": [],
    }
    try:
        local_config = read_local_config(config_root)
    except LocalConfigError as exc:
        payload["ok"] = False
        payload["errors"].append(f"local_config: {exc}")
        local_config = None
    if local_config is not None:
        if local_config.author_guard:
            payload["author_guard_active"] = True
            payload["author_guard"] = dict(local_config.author_guard)

    try:
        config = load_config(config_root)
    except Exception as exc:
        payload["ok"] = False
        payload["errors"].append(f"config: {type(exc).__name__}: {exc}")
        return payload

    payload["project"] = config.project
    payload["api_url_configured"] = bool(config.api_url)
    cached_token = read_cached_token(config.api_url.rstrip("/")) if config.api_url else None
    payload["token_cached"] = cached_token is not None
    payload["token_expires_at"] = None if cached_token is None else cached_token["expires_at"]
    payload["worker"] = config.worker_key()
    payload["worker_present"] = config.worker is not None
    return payload


async def _load_api_config(
    root: Path,
    ctx: Context | None = None,
) -> tuple[IssuekitConfig, Path]:
    config_root = await _resolve_config_root(root, ctx)
    config = load_config(config_root)
    if not config.api_url:
        raise WorkflowError(_missing_api_url_message(config_root), code="missing_api_url")
    return config, config_root


@asynccontextmanager
async def _api_config(
    root: Path,
    ctx: Context | None = None,
) -> AsyncIterator[tuple[IssuekitConfig, Path]]:
    yield await _load_api_config(root, ctx)


@asynccontextmanager
async def _api_store(
    root: Path,
    ctx: Context | None = None,
) -> AsyncIterator[tuple[IssuekitConfig, Path, Any]]:
    config, config_root = await _load_api_config(root, ctx)
    with get_store(config) as store:
        yield config, config_root, store


async def _resolve_config_root(root: Path, ctx: Context | None = None) -> Path:
    root = root.resolve()
    local_root = _configured_root(root)
    if local_root is not None:
        return local_root

    for client_root in await _client_roots(ctx):
        configured = _configured_root(client_root)
        if configured is not None:
            return configured

    return root


def _configured_root(root: Path) -> Path | None:
    root = root.resolve()
    if _has_config_candidate(root):
        return root
    repository_root = git_root(root)
    # A machine API config is sufficient for an existing repository root, but
    # does not make an arbitrary MCP process directory a project context.
    if repository_root is not None and (
        _has_config_candidate(repository_root) or _machine_config_has_api_url()
    ):
        return repository_root
    return None


def _has_config_candidate(root: Path) -> bool:
    if (root / "issuekit.toml").exists():
        return True
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.exists():
        return False
    try:
        data = load_toml(pyproject_path)
    except LocalConfigError:
        return True
    tool_config = data.get("tool")
    return isinstance(tool_config, dict) and "issuekit" in tool_config


def _machine_config_has_api_url() -> bool:
    machine_path = resolve_machine_config_path()
    if machine_path is None or not machine_path.is_file():
        return False
    try:
        data = load_toml(machine_path)
    except LocalConfigError:
        return False
    return bool(str(data.get("api_url", "")).strip())


async def _client_roots(ctx: Context | None) -> tuple[Path, ...]:
    if ctx is None:
        return ()
    try:
        request_context = ctx.request_context
    except ValueError:
        return ()
    try:
        result = await request_context.session.list_roots()
    except Exception:
        return ()
    paths: list[Path] = []
    for root in result.roots:
        path = _path_from_file_uri(str(root.uri))
        if path is not None:
            paths.append(path)
    return tuple(paths)


def _path_from_file_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    return Path(path)


def _missing_api_url_message(root: Path) -> str:
    machine_path = resolve_machine_config_path()
    machine_config = "disabled" if machine_path is None else str(machine_path)
    machine_exists = machine_path is not None and machine_path.is_file()
    return (
        "API store requires api_url. Set api_url in issuekit.toml/[tool.issuekit] "
        "or ISSUEKIT_API_URL. MCP resolved the repository root to "
        f"{root.resolve()} and searched {root / 'pyproject.toml'} [tool.issuekit], "
        f"{root / 'issuekit.toml'}, and {root / '.env'}. Machine config: "
        f"{machine_config} (exists: {machine_exists}). If the CLI succeeds, it is "
        "probably running from a different working directory or shell environment; "
        "launch issuekit-mcp from the repo root, configure the MCP client workspace "
        "root, or ensure the MCP server process receives the same ISSUEKIT_CONFIG, "
        "HOME, or XDG_CONFIG_HOME setting as the CLI. To continue reviewing before "
        "that is resolved, `issuekit show <id>` and `issuekit next-review` are "
        "read-only CLI equivalents of the get_issue and next_review tools."
    )


def main() -> None:
    asyncio.run(create_server().run_stdio_async())
