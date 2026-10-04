"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from issuekit.agents.proposal_check import list_worker_proposal_checks
from issuekit.guards.author import guard_dict, stop_message
from issuekit.mcp.runtime import MCP_SESSION, McpRuntime
from issuekit.proposals.adopt import adopt_proposal_with_append
from issuekit.proposals.checks import request_proposal_check
from issuekit.proposals.outgoing import list_outgoing_proposals
from issuekit.proposals.send import discard_proposal as discard_proposal_service
from issuekit.proposals.send import propose_with_guard
from issuekit.proposals.service import list_incoming_proposals


def register(server: FastMCP, rt: McpRuntime) -> None:
    @server.tool(
        description=(
            "Send a cross-repository proposal from the origin project to the target "
            "project inbox; use this instead of authoring directly in the target repo. "
            "Pass depends_on as project#N, project#issue:N, or project#proposal:N "
            "for upstream dependencies. A payload_mismatch response has ok=false "
            "and means the pending proposal does not match your requested text."
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
        async with rt.api_config(ctx) as (config, config_root):
            outcome = propose_with_guard(
                config_root,
                config,
                to=to,
                title=title,
                body=body,
                body_file=None,
                from_issue=from_issue,
                reply=reply,
                blocking=blocking,
                depends_on=depends_on,
                author_agent=agent,
                session=MCP_SESSION,
            )
        sent = outcome.sent
        if outcome.mismatched:
            return {**sent, "ok": False}
        if outcome.deduplicated:
            return sent
        guard = outcome.guard
        sent = dict(sent)
        sent["authorGuard"] = guard_dict(guard)
        sent["stop"] = stop_message(guard)
        return sent

    @server.tool(description="List incoming cross-repository proposals.")
    async def list_incoming(ctx: Context | None = None) -> list[dict[str, Any]]:
        async with rt.api_config(ctx) as (config, _config_root):
            return list_incoming_proposals(config)

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
        async with rt.api_config(ctx) as (config, _config_root):
            return list_outgoing_proposals(config, to=to, status=status)

    @server.tool(description="Adopt an incoming proposal as a local active issue.")
    async def adopt_proposal(
        proposal_id: int,
        priority: str = "medium",
        append: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_config(ctx) as (config, _config_root):
            return adopt_proposal_with_append(
                config,
                proposal_id,
                priority=priority,
                append_text=append,
            )

    @server.tool(
        description=(
            "Discard a pending cross-repository proposal. Discards an incoming "
            "proposal by default; pass `to` to discard a proposal this project "
            "sent to that target project's inbox instead."
        )
    )
    async def discard_proposal(
        proposal_id: int,
        to: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_config(ctx) as (config, _config_root):
            return discard_proposal_service(config, proposal_id, to=to)

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
        async with rt.api_config(ctx) as (config, _config_root):
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
        async with rt.api_config(ctx) as (config, _config_root):
            return list_worker_proposal_checks(
                config,
                status=status,
                limit=limit,
                offset=offset,
            )
