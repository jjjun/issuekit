"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from issuekit.mcp.runtime import McpRuntime
from issuekit.negotiation import ThreadStatus, get_negotiation_store
from issuekit.negotiation.engine import load_thread_inspection


def register(server: FastMCP, rt: McpRuntime) -> None:
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
        async with rt.api_config(ctx) as (config, _config_root):
            with get_negotiation_store(config, use_mock=mock) as store:
                if thread_id:
                    return load_thread_inspection(store, thread_id).to_dict()
                thread_status = ThreadStatus(status) if status else None
                return [
                    summary.to_dict()
                    for summary in store.list_threads(status=thread_status)
                ]
