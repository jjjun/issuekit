"""Implementation of the setup command."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import json
from pathlib import Path
import shutil
import tomllib

from issuekit.commands.init import (
    CODEX_MCP_HEADER,
    HANDOFF_HEADER,
    InitResult,
    LOCAL_GITIGNORE_ENTRIES,
    _display_path,
    init_repo,
)
from issuekit.config import load_config
from issuekit.core import build_index_files, read_all_issues


CODEX_MCP_ADD_COMMAND = "codex mcp add issuekit -- issuekit-mcp"
MCP_INSTALL_COMMAND = 'uv tool install "issuekit[mcp] @ <absolute-path-or-url>"'
MCP_REINSTALL_COMMAND = 'uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"'


@dataclass(frozen=True)
class Diagnostic:
    status: str
    label: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupAction:
    path: str
    state: str
    action: str
    reason: str


def run(args) -> int:
    cwd = Path.cwd()
    if getattr(args, "check", False) or getattr(args, "setup_action", None) == "check":
        payload = build_check_json_payload(cwd)
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        _print_check_payload(payload)
        return 0

    result = init_repo(cwd, force=args.force, with_mcp=True)
    if args.json:
        print(json.dumps(build_json_payload(cwd, result), indent=2))
        return 0
    _print_init_result(result)
    _print_diagnostics(cwd)
    return 0


def build_json_payload(cwd: Path, result: InitResult) -> dict[str, object]:
    diagnostics = collect_diagnostics(cwd)
    return {
        "ok": all(diagnostic.status != "ACTION" for diagnostic in diagnostics),
        "scaffold": {
            "written": result.written,
            "skipped": result.skipped,
            "guidance": result.guidance,
        },
        "diagnostics": [
            {
                "status": diagnostic.status,
                "label": diagnostic.label,
                "details": list(diagnostic.details),
            }
            for diagnostic in diagnostics
        ],
    }


def build_check_json_payload(cwd: Path) -> dict[str, object]:
    actions = collect_setup_actions(cwd)
    diagnostics = collect_diagnostics(cwd)
    states = {action.state for action in actions}
    if "blocked" in states:
        state = "blocked"
    elif "stale" in states:
        state = "stale"
    elif "missing" in states:
        state = "missing"
    else:
        state = "current"
    return {
        "ok": not actions and all(diagnostic.status != "ACTION" for diagnostic in diagnostics),
        "state": state,
        "needs_setup": bool(actions),
        "would_write": any(action.action == "write" for action in actions),
        "would_update": any(action.action in {"update", "remove"} for action in actions),
        "diagnostics": [
            {
                "status": diagnostic.status,
                "label": diagnostic.label,
                "details": list(diagnostic.details),
            }
            for diagnostic in diagnostics
        ],
        "actions": [
            {
                "path": action.path,
                "state": action.state,
                "action": action.action,
                "reason": action.reason,
            }
            for action in actions
        ],
    }


def collect_diagnostics(cwd: Path) -> list[Diagnostic]:
    return [
        _issuekit_mcp_command_diagnostic(),
        _issuekit_mcp_import_diagnostic(),
        _mcp_json_diagnostic(cwd),
        _codex_config_diagnostic(cwd),
        _handoff_reference_diagnostic(cwd, "AGENTS.md"),
        _handoff_reference_diagnostic(cwd, "CLAUDE.md"),
    ]


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


def _print_init_result(result: InitResult) -> None:
    for path in result.written:
        print(f"Wrote: {path}")
    for path in result.skipped:
        print(f"Skipped existing: {path}")
    for item in result.guidance:
        print(item)


def _print_check_payload(payload: dict[str, object]) -> None:
    print(f"Setup check: {payload['state']}")
    print(f"Needs setup: {str(payload['needs_setup']).lower()}")
    for action in payload["actions"]:
        print(f"[{action['state'].upper()}] {action['path']}: {action['reason']}")


def _print_diagnostics(cwd: Path) -> None:
    print()
    print("Setup diagnostics:")
    for diagnostic in collect_diagnostics(cwd):
        print(f"[{diagnostic.status}] {diagnostic.label}")
        for detail in diagnostic.details:
            print(f"  {detail}")
    print()
    print("Codex MCP guidance:")
    print(f"  {CODEX_MCP_ADD_COMMAND}")
    print("  Optional when codex reads this repo's .codex/config.toml.")
    print("  Use it only when managing issuekit through the global codex MCP store.")
    print()
    print("Global tool update guidance:")
    print("  Stop running issuekit MCP servers before reinstalling or upgrading.")
    print("  MCP clients hold issuekit-mcp.exe while the session is running.")
    print(f"  Then run: {MCP_REINSTALL_COMMAND}")
    print("  Use an absolute path or URL, never a bare '.', to avoid cwd-dependent installs.")
    print("  Restart running agent sessions after any reinstall or upgrade.")
    print("  Existing codex or Claude Code sessions keep a stdio connection to the old server.")


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
    for name, content in expected.items():
        path = indexes_dir / name
        display = _display_path(cwd, path)
        if not path.exists():
            actions.append(
                SetupAction(display, "missing", "write", "issuekit setup would generate this index.")
            )
            continue
        current = path.read_text(encoding="utf-8-sig", errors="ignore")
        if current != content:
            actions.append(
                SetupAction(display, "stale", "update", "issuekit setup would refresh this index.")
            )
    if not indexes_dir.exists():
        return
    for path in sorted(indexes_dir.glob("*.md")):
        if path.name not in expected:
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


def _issuekit_mcp_command_diagnostic() -> Diagnostic:
    path = shutil.which("issuekit-mcp")
    if path:
        return Diagnostic("OK", "issuekit-mcp command is on PATH.", (f"Found: {path}",))
    return Diagnostic(
        "ACTION",
        "issuekit-mcp command is not on PATH.",
        (f"Install the global tool with the MCP extra: {MCP_INSTALL_COMMAND}",),
    )


def _issuekit_mcp_import_diagnostic() -> Diagnostic:
    try:
        import_module("issuekit.mcp.server")
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
