"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from issuekit.mcp.runtime import McpRuntime
from issuekit.mcp.tools import issues, lifecycle, meta, negotiation, proposals, workers
from issuekit.prompts.protocol import render_server_instructions


def create_server(
    cwd: Path | str | None = None,
    *,
    allow_overrides: bool = False,
) -> FastMCP:
    root = Path.cwd() if cwd is None else Path(cwd)
    rt = McpRuntime(root, allow_overrides=allow_overrides)
    server = FastMCP("issuekit", instructions=render_server_instructions())
    meta.register(server, rt)
    lifecycle.register(server, rt)
    issues.register(server, rt)
    workers.register(server, rt)
    proposals.register(server, rt)
    negotiation.register(server, rt)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="issuekit-mcp")
    parser.add_argument(
        "--allow-overrides",
        action="store_true",
        help="Expose human emergency recovery tools and parameters.",
    )
    args = parser.parse_args()
    asyncio.run(create_server(allow_overrides=args.allow_overrides).run_stdio_async())
