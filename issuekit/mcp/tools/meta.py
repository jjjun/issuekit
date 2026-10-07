"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from issuekit.mcp.runtime import McpRuntime, _health_status
from issuekit.prompts.protocol import render_protocol


def register(server: MCPServer, rt: McpRuntime) -> None:
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
        return await rt.run_config(
            ctx,
            lambda config, _config_root: render_protocol(
                agent, role=role, agent_roles=config.agent_roles
            ),
        )
