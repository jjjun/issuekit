"""Implementation of the setup command."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import json
import shutil

from issuekit.commands.init import InitResult, init_repo
from issuekit.commands.setup.actions import SetupAction, collect_setup_actions
from issuekit.commands.setup.diagnostics import Diagnostic, collect_diagnostics as _collect_diagnostics


CODEX_MCP_ADD_COMMAND = "codex mcp add issuekit -- issuekit-mcp"
MCP_INSTALL_COMMAND = 'uv tool install "issuekit[mcp] @ <absolute-path-or-url>"'
MCP_REINSTALL_COMMAND = 'uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"'


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
    return _collect_diagnostics(cwd, which=shutil.which, import_module_fn=import_module)


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
