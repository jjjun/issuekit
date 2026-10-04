"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from issuekit.mcp.runtime import McpRuntime
from issuekit.proposals.client import api_client
from issuekit.workers.registry import list_api_workers, remove_api_repo, remove_api_worker


def register(server: FastMCP, rt: McpRuntime) -> None:
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
        async with rt.api_config(ctx) as (config, _config_root):
            return list_api_workers(config, repo_id=repo_id, project=project)

    if rt.allow_overrides:
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
            async with rt.api_config(ctx) as (config, _config_root):
                result = remove_api_worker(config, address, force=force)
            return result.to_dict()

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
            async with rt.api_config(ctx) as (config, _config_root):
                result = remove_api_repo(config, repo)
            return result.to_dict()

    @server.tool(
        description=(
            "List stored project capability profiles across projects (the PM "
            "router's input). Requires a backend that supports project profiles."
        )
    )
    async def list_project_profiles(ctx: Context | None = None) -> list[dict[str, Any]]:
        async with rt.api_config(ctx) as (config, _config_root):
            with api_client(config) as client:
                return client.list_project_profiles()
