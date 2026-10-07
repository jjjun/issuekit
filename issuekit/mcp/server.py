"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import argparse
import asyncio
import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from issuekit import __version__
from issuekit.mcp.runtime import McpRuntime
from issuekit.mcp.tools import issues, lifecycle, meta, negotiation, proposals, workers
from issuekit.prompts.protocol import render_server_instructions


class IssuekitServer(MCPServer):
    def tool(
        self, *args: Any, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        register = super().tool(*args, **kwargs)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            async def wrapped(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    return await fn(*call_args, **call_kwargs)
                except (ToolError, MCPError):
                    raise
                except Exception as exc:
                    raise ToolError(str(exc)) from exc

            return register(wrapped)

        return decorator


def create_server(
    cwd: Path | str | None = None,
    *,
    allow_overrides: bool = False,
) -> MCPServer:
    root = Path.cwd() if cwd is None else Path(cwd)
    rt = McpRuntime(root, allow_overrides=allow_overrides)
    server = IssuekitServer(
        "issuekit", instructions=render_server_instructions(), version=__version__
    )
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
