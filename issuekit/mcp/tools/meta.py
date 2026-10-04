"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from issuekit.config import load_config
from issuekit.mcp.runtime import McpRuntime, _health_status
from issuekit.prompts.protocol import render_protocol


def register(server: FastMCP, rt: McpRuntime) -> None:
    @server.tool(
        description=(
            "Read-only MCP server health and configuration status. Safe to call "
            "before workflow operations."
        )
    )
    async def health(ctx: Context) -> dict[str, Any]:
        return await _health_status(rt.root, ctx)

    @server.tool(
        description=(
            "Pass role= (author, implementer, reviewer, triage, pm) to get one role's "
            "steps; with no arguments it returns every role."
        )
    )
    async def get_protocol(
        agent: str | None = None,
        role: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        config_root = await rt.config_root(ctx)
        config = load_config(config_root)
        return render_protocol(agent, role=role, agent_roles=config.agent_roles)
