"""Action planning helpers for setup command."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from issuekit.commands.init import (
    CLAUDE_AGENTS_IMPORT,
    CODEX_MCP_HEADER,
    HANDOFF_HEADER,
    append_codex_issuekit_table,
    find_codex_issuekit_table,
)
from issuekit.config.local import missing_gitignore_entries


@dataclass(frozen=True)
class SetupAction:
    path: str
    state: str
    action: str
    reason: str


def collect_setup_actions(cwd: Path) -> list[SetupAction]:
    actions: list[SetupAction] = []
    _add_missing_file_actions(
        cwd,
        actions,
        (
            ".gitattributes",
            ".editorconfig",
            ".pre-commit-config.yaml",
        ),
    )
    _add_pre_commit_action(cwd, actions)
    _add_gitignore_action(cwd, actions)
    _add_mcp_json_action(cwd, actions)
    _add_codex_config_action(cwd, actions)
    _add_handoff_action(cwd, actions, "AGENTS.md")
    _add_claude_import_action(cwd, actions)
    return actions


def _add_missing_file_actions(
    cwd: Path,
    actions: list[SetupAction],
    relative_paths: tuple[str, ...],
) -> None:
    for relative_path in relative_paths:
        if not (cwd / relative_path).exists():
            actions.append(
                SetupAction(
                    relative_path,
                    "missing",
                    "write",
                    "issuekit setup would create this scaffold file.",
                )
            )


def _add_gitignore_action(cwd: Path, actions: list[SetupAction]) -> None:
    path = cwd / ".gitignore"
    if not path.exists():
        actions.append(
            SetupAction(
                ".gitignore",
                "missing",
                "write",
                "issuekit setup would create .gitignore with issuekit local entries.",
            )
        )
        return
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if missing_gitignore_entries(content):
        actions.append(
            SetupAction(
                ".gitignore",
                "stale",
                "update",
                "issuekit setup would add missing issuekit local entries.",
            )
        )


def _add_pre_commit_action(cwd: Path, actions: list[SetupAction]) -> None:
    path = cwd / ".pre-commit-config.yaml"
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    missing_hooks = []
    if "issuekit check-encoding" not in content:
        missing_hooks.append("issuekit check-encoding hook")
    if "issuekit author-guard check" not in content:
        missing_hooks.append("optional author-session guard hook")
    if missing_hooks:
        missing = " and ".join(missing_hooks)
        actions.append(
            SetupAction(
                ".pre-commit-config.yaml",
                "blocked",
                "manual",
                "issuekit setup cannot edit an existing pre-commit file. Add the "
                f"{missing} manually.",
            )
        )


def _add_mcp_json_action(cwd: Path, actions: list[SetupAction]) -> None:
    path = cwd / ".mcp.json"
    if not path.exists():
        actions.append(SetupAction(".mcp.json", "missing", "write", "issuekit setup would create it."))
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        actions.append(
            SetupAction(
                ".mcp.json",
                "blocked",
                "manual",
                "invalid JSON blocks issuekit setup from safely merging this file.",
            )
        )
        return
    if not isinstance(data, dict):
        actions.append(
            SetupAction(
                ".mcp.json",
                "blocked",
                "manual",
                "the file must contain a JSON object before issuekit setup can merge it.",
            )
        )
        return
    servers = data.get("mcpServers")
    if servers is None:
        actions.append(
            SetupAction(
                ".mcp.json",
                "stale",
                "update",
                "issuekit setup would add mcpServers.issuekit.",
            )
        )
        return
    if not isinstance(servers, dict):
        actions.append(
            SetupAction(
                ".mcp.json",
                "blocked",
                "manual",
                "mcpServers must be a JSON object before issuekit setup can merge it.",
            )
        )
        return
    if "issuekit" not in servers:
        actions.append(
            SetupAction(
                ".mcp.json",
                "stale",
                "update",
                "issuekit setup would add mcpServers.issuekit.",
            )
        )


def _add_codex_config_action(cwd: Path, actions: list[SetupAction]) -> None:
    path = cwd / ".codex" / "config.toml"
    display = ".codex/config.toml"
    if not path.exists():
        actions.append(SetupAction(display, "missing", "write", "issuekit setup would create it."))
        return
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        actions.append(
            SetupAction(
                display,
                "blocked",
                "manual",
                "invalid TOML should be fixed before issuekit setup can merge this file.",
            )
        )
        return
    servers = parsed.get("mcp_servers")
    issuekit_server = servers.get("issuekit") if isinstance(servers, dict) else None
    if issuekit_server is not None:
        if isinstance(issuekit_server, dict) and "env_vars" in issuekit_server:
            return
        if find_codex_issuekit_table(content) is not None:
            actions.append(
                SetupAction(
                    display,
                    "stale",
                    "update",
                    "issuekit setup would add the missing env_vars to "
                    f"{CODEX_MCP_HEADER}.",
                )
            )
        else:
            actions.append(
                SetupAction(
                    display,
                    "blocked",
                    "manual",
                    "the issuekit server is not a standard table; add its missing "
                    "env_vars manually.",
                )
            )
        return

    try:
        tomllib.loads(append_codex_issuekit_table(content, "[mcp_servers.issuekit]"))
    except tomllib.TOMLDecodeError:
        actions.append(
            SetupAction(
                display,
                "blocked",
                "manual",
                "issuekit setup cannot add [mcp_servers.issuekit] to this TOML shape.",
            )
        )
        return
    actions.append(
        SetupAction(
            display,
            "stale",
            "update",
            "issuekit setup would append [mcp_servers.issuekit].",
        )
    )


def _add_handoff_action(cwd: Path, actions: list[SetupAction], filename: str) -> None:
    path = cwd / filename
    if not path.exists():
        actions.append(SetupAction(filename, "missing", "write", "issuekit setup would create it."))
        return
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if HANDOFF_HEADER not in content:
        actions.append(
            SetupAction(
                filename,
                "stale",
                "update",
                "issuekit setup would append the handoff reference.",
            )
        )


def _add_claude_import_action(cwd: Path, actions: list[SetupAction]) -> None:
    path = cwd / "CLAUDE.md"
    if not path.exists():
        actions.append(
            SetupAction(
                "CLAUDE.md",
                "missing",
                "write",
                "issuekit setup would create it with an @AGENTS.md import.",
            )
        )
        return

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if any(line.strip() == CLAUDE_AGENTS_IMPORT for line in content.splitlines()):
        return

    actions.append(
        SetupAction(
            "CLAUDE.md",
            "blocked",
            "manual",
            "CLAUDE.md does not import AGENTS.md. Add `@AGENTS.md` so Claude Code "
            "reads the shared repository guidance.",
        )
    )
