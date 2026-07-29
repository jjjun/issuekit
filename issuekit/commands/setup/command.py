"""Implementation of the setup command."""

from __future__ import annotations

import argparse
import shutil
from importlib import import_module
from pathlib import Path

from issuekit.commands._common import print_json
from issuekit.commands.init import InitResult, init_repo
from issuekit.commands.setup.actions import collect_setup_actions
from issuekit.commands.setup.diagnostics import Diagnostic
from issuekit.commands.setup.diagnostics import collect_diagnostics as _collect_diagnostics

CODEX_MCP_ADD_COMMAND = "codex mcp add issuekit -- issuekit-mcp"
MCP_INSTALL_COMMAND = 'uv tool install "issuekit[mcp] @ <absolute-path-or-url>"'
MCP_REINSTALL_COMMAND = 'uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"'
CLIENT_TRANSPORT_CHECK = {
    "status": "unsupported_from_cli",
    "message": (
        "issuekit setup check verifies static files and importability only. "
        "A standalone CLI cannot prove that an already-open Codex or Claude "
        "stdio MCP transport is live."
    ),
}


def register(subparsers: argparse._SubParsersAction) -> None:
    setup_parser = subparsers.add_parser(
        "setup",
        help="Initialize repo MCP handoff scaffolding and print setup diagnostics.",
    )
    _add_setup_apply_options(setup_parser)
    setup_parser.add_argument(
        "--check",
        action="store_true",
        help="Check setup state without writing files.",
    )
    setup_subparsers = setup_parser.add_subparsers(dest="setup_action", metavar="<action>")
    setup_check_parser = setup_subparsers.add_parser(
        "check",
        help="Check setup state without writing files.",
    )
    _add_setup_check_options(setup_check_parser)
    setup_check_parser.set_defaults(func=run)
    setup_apply_parser = setup_subparsers.add_parser(
        "apply",
        help="Initialize repo MCP handoff scaffolding and print setup diagnostics.",
    )
    _add_setup_apply_options(setup_apply_parser)
    setup_apply_parser.set_defaults(func=run)
    setup_parser.set_defaults(func=run)


def _add_setup_check_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )


def _add_setup_apply_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing templated files.",
    )
    _add_setup_check_options(parser)


def run(args) -> int:
    cwd = Path.cwd()
    if getattr(args, "check", False) or getattr(args, "setup_action", None) == "check":
        payload = build_check_json_payload(cwd)
        if args.json:
            print_json(payload)
            return 0
        _print_check_payload(payload)
        return 0

    result = init_repo(cwd, force=args.force, with_mcp=True)
    if args.json:
        print_json(build_json_payload(cwd, result))
        return 0
    _print_init_result(result)
    _print_diagnostics(cwd)
    return 0


def build_json_payload(cwd: Path, result: InitResult) -> dict[str, object]:
    diagnostics = collect_diagnostics(cwd)
    return {
        "ok": all(diagnostic.status != "ACTION" for diagnostic in diagnostics),
        "client_transport_check": CLIENT_TRANSPORT_CHECK,
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
        "client_transport_check": CLIENT_TRANSPORT_CHECK,
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
    transport_check = payload["client_transport_check"]
    print(f"Client transport check: {transport_check['status']}")
    print(f"  {transport_check['message']}")
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
