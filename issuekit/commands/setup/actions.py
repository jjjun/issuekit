"""Action planning helpers for setup command."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib

from issuekit.commands.init import (
    CODEX_MCP_HEADER,
    HANDOFF_HEADER,
    LOCAL_GITIGNORE_ENTRIES,
    _display_path,
)
from issuekit.config import load_config
from issuekit.core import build_index_files, diff_index_files, read_all_issues


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
            "docs/issues/incoming/.gitkeep",
            ".gitattributes",
            ".editorconfig",
            "docs/issues/README.md",
            ".pre-commit-config.yaml",
        ),
    )
    _add_gitignore_action(cwd, actions)
    _add_index_actions(cwd, actions)
    _add_mcp_json_action(cwd, actions)
    _add_codex_config_action(cwd, actions)
    _add_handoff_action(cwd, actions, "AGENTS.md")
    _add_handoff_action(cwd, actions, "CLAUDE.md")
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
    entries = {line.strip() for line in content.splitlines()}
    missing_entries = [
        entry
        for entry in LOCAL_GITIGNORE_ENTRIES
        if entry not in entries and not (entry == ".agent-runs/" and ".agent-runs" in entries)
    ]
    if missing_entries:
        actions.append(
            SetupAction(
                ".gitignore",
                "stale",
                "update",
                "issuekit setup would add missing issuekit local entries.",
            )
        )


def _add_index_actions(cwd: Path, actions: list[SetupAction]) -> None:
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)
    indexes_dir = issues_dir / "indexes"
    active_issues, completed_issues, _ = read_all_issues(issues_dir)
    expected = build_index_files(active_issues, completed_issues, config.recent_count)
    index_diff = diff_index_files(issues_dir, expected)
    stale_set = set(index_diff.stale)
    missing_set = set(index_diff.missing)
    for name in expected:
        if name in missing_set:
            path = indexes_dir / name
            actions.append(
                SetupAction(
                    _display_path(cwd, path),
                    "missing",
                    "write",
                    "issuekit setup would generate this index.",
                )
            )
            continue
        if name in stale_set:
            path = indexes_dir / name
            actions.append(
                SetupAction(
                    _display_path(cwd, path),
                    "stale",
                    "update",
                    "issuekit setup would refresh this index.",
                )
            )
    for name in index_diff.extra:
        path = indexes_dir / name
        actions.append(
            SetupAction(
                _display_path(cwd, path),
                "stale",
                "remove",
                "issuekit setup would remove this obsolete generated index.",
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
                "invalid TOML should be fixed before issuekit setup appends to this file.",
            )
        )
        return
    servers = parsed.get("mcp_servers")
    if isinstance(servers, dict) and "issuekit" in servers:
        return
    if CODEX_MCP_HEADER in content:
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
