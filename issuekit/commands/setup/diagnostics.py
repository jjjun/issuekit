"""Diagnostic helpers for setup command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
import json
from importlib import import_module
import shutil
import tomllib

from issuekit.author_guard import read_author_guard
from issuekit.commands.init import CODEX_MCP_HEADER, HANDOFF_HEADER


MCP_INSTALL_COMMAND = 'uv tool install "issuekit[mcp] @ <absolute-path-or-url>"'


@dataclass(frozen=True)
class Diagnostic:
    status: str
    label: str
    details: tuple[str, ...] = ()


def collect_diagnostics(
    cwd: Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    import_module_fn: Callable[[str], object] = import_module,
) -> list[Diagnostic]:
    return [
        _issuekit_mcp_command_diagnostic(which),
        _issuekit_mcp_import_diagnostic(import_module_fn),
        _mcp_json_diagnostic(cwd),
        _codex_config_diagnostic(cwd),
        _handoff_reference_diagnostic(cwd, "AGENTS.md"),
        _handoff_reference_diagnostic(cwd, "CLAUDE.md"),
        _author_guard_diagnostic(cwd),
    ]


def _issuekit_mcp_command_diagnostic(
    which: Callable[[str], str | None],
) -> Diagnostic:
    path = which("issuekit-mcp")
    if path:
        return Diagnostic("OK", "issuekit-mcp command is on PATH.", (f"Found: {path}",))
    return Diagnostic(
        "ACTION",
        "issuekit-mcp command is not on PATH.",
        (f"Install the global tool with the MCP extra: {MCP_INSTALL_COMMAND}",),
    )


def _issuekit_mcp_import_diagnostic(import_module_fn: Callable[[str], object]) -> Diagnostic:
    try:
        import_module_fn("issuekit.mcp.server")
    except Exception as exc:
        return Diagnostic(
            "ACTION",
            "issuekit MCP server dependencies are not importable.",
            (
                f"{type(exc).__name__}: {exc}",
                f"Install the global tool with the MCP extra: {MCP_INSTALL_COMMAND}",
            ),
        )
    return Diagnostic("OK", "issuekit MCP server module is importable.")


def _mcp_json_diagnostic(cwd: Path) -> Diagnostic:
    path = cwd / ".mcp.json"
    if not path.exists():
        return Diagnostic("ACTION", ".mcp.json is missing.", ("Run issuekit setup.",))
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return Diagnostic("ACTION", ".mcp.json is not valid JSON.", (str(exc),))
    if not isinstance(data, dict):
        return Diagnostic("ACTION", ".mcp.json must contain a JSON object.")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "issuekit" not in servers:
        return Diagnostic(
            "ACTION",
            ".mcp.json does not contain an issuekit MCP server.",
            ("Add mcpServers.issuekit with command issuekit-mcp.",),
        )
    return Diagnostic("OK", ".mcp.json contains an issuekit MCP server.")


def _codex_config_diagnostic(cwd: Path) -> Diagnostic:
    path = cwd / ".codex" / "config.toml"
    if not path.exists():
        return Diagnostic(
            "ACTION",
            ".codex/config.toml is missing.",
            ("Run issuekit setup or configure codex through the global MCP store.",),
        )
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        return Diagnostic("ACTION", ".codex/config.toml is not valid TOML.", (str(exc),))
    servers = parsed.get("mcp_servers")
    if isinstance(servers, dict) and "issuekit" in servers:
        return Diagnostic("OK", ".codex/config.toml contains [mcp_servers.issuekit].")
    if CODEX_MCP_HEADER in content:
        return Diagnostic("OK", ".codex/config.toml contains [mcp_servers.issuekit].")
    return Diagnostic(
        "ACTION",
        ".codex/config.toml does not contain [mcp_servers.issuekit].",
        ("Run issuekit setup or use the codex mcp add guidance below.",),
    )


def _handoff_reference_diagnostic(cwd: Path, filename: str) -> Diagnostic:
    path = cwd / filename
    if not path.exists():
        return Diagnostic("ACTION", f"{filename} is missing.", ("Run issuekit setup.",))
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if HANDOFF_HEADER in content:
        return Diagnostic("OK", f"{filename} contains the handoff reference.")
    return Diagnostic(
        "ACTION",
        f"{filename} does not contain the handoff reference.",
        ("Run issuekit setup.",),
    )


def _author_guard_diagnostic(cwd: Path) -> Diagnostic:
    guard = read_author_guard(cwd)
    if guard is None:
        return Diagnostic("OK", "No local author-session guard is active.")
    return Diagnostic(
        "WARN",
        "Local author-session guard is active.",
        (
            f"STOP_NOW: authored {guard.kind} {guard.ref or guard.id}.",
            "Stop this session before implementing, or run issuekit author-guard clear after handoff.",
        ),
    )
