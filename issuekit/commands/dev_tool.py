"""Developer global-tool maintenance commands."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from collections.abc import Callable, Sequence
import json
import platform
import subprocess
import sys


MCP_PROCESS_NAME = "issuekit-mcp.exe"
MCP_PROCESS_NAME_POSIX = "issuekit-mcp"
CLIENT_TRANSPORT_UNSUPPORTED_MESSAGE = (
    "Standalone issuekit CLI commands can inspect local setup and stop matching "
    "issuekit-mcp processes, but they cannot verify, reconnect, or respawn an "
    "already-open Codex or Claude stdio MCP transport."
)
RELOAD_BOUNDARY_MESSAGE = (
    "reload-mcp only stops matching issuekit-mcp processes. If MCP tools still "
    "return Transport closed, reload or restart the MCP client session, reload "
    "the thread/window if supported, or start a fresh session so the client can "
    "spawn a new issuekit-mcp stdio transport."
)
NO_PROCESS_RELOAD_MESSAGE = (
    "No issuekit-mcp processes were stopped. This does not reconnect an "
    "already-open Codex or Claude stdio transport; reload or restart the MCP "
    "client session, reload the thread/window if supported, or start a fresh "
    "session after the client respawns issuekit-mcp."
)


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WindowsProcess:
    pid: int
    executable_path: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class PosixProcess:
    pid: int
    args: str


Runner = Callable[[Sequence[str]], CommandResult]


def register(subparsers: argparse._SubParsersAction) -> None:
    dev_tool_parser = subparsers.add_parser(
        "dev-tool",
        help="Maintain the global issuekit tool during local development.",
    )
    dev_tool_subparsers = dev_tool_parser.add_subparsers(
        dest="dev_tool_action",
        metavar="<action>",
        required=True,
    )
    install_editable_parser = dev_tool_subparsers.add_parser(
        "install-editable",
        help="Install this checkout as the global issuekit tool in editable mode.",
    )
    install_editable_parser.add_argument(
        "--repo",
        help="Explicit issuekit checkout path; defaults to the installed source root.",
    )
    install_editable_parser.add_argument(
        "--no-stop",
        action="store_true",
        help="Do not stop running issuekit-mcp.exe processes before installing.",
    )
    install_editable_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    install_editable_parser.set_defaults(func=run)

    reinstall_parser = dev_tool_subparsers.add_parser(
        "reinstall",
        help="Reinstall this checkout as the global issuekit tool using an absolute path.",
    )
    reinstall_parser.add_argument(
        "--repo",
        help="Explicit issuekit checkout path; defaults to the installed source root.",
    )
    reinstall_parser.add_argument(
        "--no-stop",
        action="store_true",
        help="Do not stop running issuekit-mcp.exe processes before reinstalling.",
    )
    reinstall_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    reinstall_parser.set_defaults(func=run)

    reload_mcp_parser = dev_tool_subparsers.add_parser(
        "reload-mcp",
        help="Stop running issuekit-mcp processes so clients can restart them.",
    )
    reload_mcp_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    reload_mcp_parser.set_defaults(func=run)


def run(args) -> int:
    action = getattr(args, "dev_tool_action", None)
    if action == "install-editable":
        return _run_install(args, editable=True)
    if action == "reinstall":
        return _run_install(args, editable=False)
    if action == "reload-mcp":
        return _run_reload_mcp(args)
    print("Error: dev-tool action is required.", file=sys.stderr)
    return 1


def build_editable_install_command(repo: Path) -> list[str]:
    return ["uv", "tool", "install", "--editable", f"{repo}[mcp]"]


def build_reinstall_command(repo: Path) -> list[str]:
    return ["uv", "tool", "install", "--reinstall", f"issuekit[mcp] @ {repo}"]


def build_uninstall_command() -> list[str]:
    return ["uv", "tool", "uninstall", "issuekit"]


def default_runner(argv: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CommandResult(list(argv), completed.returncode, completed.stdout, completed.stderr)


def stop_issuekit_mcp_processes(runner: Runner | None = None) -> dict[str, object]:
    if runner is None:
        runner = default_runner
    if _is_windows():
        return _stop_issuekit_mcp_processes_windows(runner)
    return _stop_issuekit_mcp_processes_posix(runner)


def _stop_issuekit_mcp_processes_windows(runner: Runner) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "actions": [],
        "stopped_processes": [],
        "commands": [],
        "diagnostics": [],
    }
    list_result = runner(_list_processes_command())
    payload["commands"].append(_command_record(list_result))
    if list_result.returncode != 0:
        payload["ok"] = False
        payload["diagnostics"].append(
            _diagnostic("error", "Failed to query issuekit-mcp.exe processes.")
        )
        return payload

    try:
        processes = filter_issuekit_mcp_processes(_parse_process_json(list_result.stdout))
    except ValueError as exc:
        payload["ok"] = False
        payload["diagnostics"].append(_diagnostic("error", str(exc)))
        return payload

    for process in processes:
        stop_result = runner(["taskkill", "/PID", str(process.pid), "/T", "/F"])
        payload["commands"].append(_command_record(stop_result))
        record = _process_record(process)
        if stop_result.returncode == 0:
            record["status"] = "stopped"
            payload["stopped_processes"].append(record)
        else:
            record["status"] = "failed"
            record["stderr"] = stop_result.stderr
            payload["stopped_processes"].append(record)
            payload["ok"] = False
    payload["actions"].append(
        {"action": "stop_mcp", "ok": payload["ok"], "count": len(payload["stopped_processes"])}
    )
    return payload


def filter_issuekit_mcp_processes(processes: Sequence[WindowsProcess]) -> list[WindowsProcess]:
    return [process for process in processes if _is_issuekit_mcp_process(process)]


def _stop_issuekit_mcp_processes_posix(runner: Runner) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "actions": [],
        "stopped_processes": [],
        "commands": [],
        "diagnostics": [],
    }
    list_result = runner(_list_processes_command_posix())
    payload["commands"].append(_command_record(list_result))
    if list_result.returncode != 0:
        payload["ok"] = False
        payload["diagnostics"].append(
            _diagnostic("error", "Failed to list processes with ps.")
        )
        return payload

    processes = filter_issuekit_mcp_processes_posix(_parse_ps_output(list_result.stdout))
    for process in processes:
        stop_result = runner(["kill", "-TERM", str(process.pid)])
        payload["commands"].append(_command_record(stop_result))
        record = _posix_process_record(process)
        if stop_result.returncode == 0:
            record["status"] = "stopped"
        else:
            record["status"] = "failed"
            record["stderr"] = stop_result.stderr
            payload["ok"] = False
        payload["stopped_processes"].append(record)
    payload["actions"].append(
        {"action": "stop_mcp", "ok": payload["ok"], "count": len(payload["stopped_processes"])}
    )
    return payload


def filter_issuekit_mcp_processes_posix(processes: Sequence[PosixProcess]) -> list[PosixProcess]:
    return [process for process in processes if _is_issuekit_mcp_process_posix(process)]


def _run_install(args, *, editable: bool, runner: Runner | None = None) -> int:
    if runner is None:
        runner = default_runner
    payload = _empty_payload()
    mode = "install_editable" if editable else "reinstall"
    if not _is_windows():
        payload["diagnostics"].append(
            _diagnostic("error", "issuekit dev-tool is currently supported only on Windows.")
        )
        return _finish(payload, json_output=args.json)

    try:
        repo = _resolve_repo_path(args.repo)
    except ValueError as exc:
        payload["diagnostics"].append(_diagnostic("error", str(exc)))
        return _finish(payload, json_output=args.json)
    payload["diagnostics"].append(_diagnostic("info", f"Repository: {repo}"))

    if not args.no_stop:
        stop_payload = stop_issuekit_mcp_processes(runner)
        _merge_payload(payload, stop_payload)
        if not stop_payload["ok"]:
            return _finish(payload, json_output=args.json)

    uninstall_result = runner(build_uninstall_command())
    payload["commands"].append(_command_record(uninstall_result))
    if uninstall_result.returncode == 0:
        payload["actions"].append({"action": "uninstall", "ok": True})
    elif _is_missing_tool_uninstall(uninstall_result):
        payload["actions"].append({"action": "uninstall", "ok": True, "non_fatal": True})
        payload["diagnostics"].append(
            _diagnostic("info", "issuekit was not installed globally; continuing.")
        )
    else:
        payload["ok"] = False
        payload["actions"].append({"action": "uninstall", "ok": False})
        payload["diagnostics"].append(_diagnostic("error", "uv tool uninstall issuekit failed."))
        return _finish(payload, json_output=args.json)

    install_command = build_editable_install_command(repo) if editable else build_reinstall_command(repo)
    install_result = runner(install_command)
    payload["commands"].append(_command_record(install_result))
    payload["actions"].append({"action": mode, "ok": install_result.returncode == 0})
    if install_result.returncode != 0:
        payload["ok"] = False
        payload["diagnostics"].append(_diagnostic("error", f"uv tool {mode} failed."))
        return _finish(payload, json_output=args.json)

    verify_payload = verify_tool_environment(repo, expect_editable=editable, runner=runner)
    _merge_payload(payload, verify_payload)
    return _finish(payload, json_output=args.json)


def _run_reload_mcp(args, runner: Runner | None = None) -> int:
    if runner is None:
        runner = default_runner
    payload = _empty_payload()
    stop_payload = stop_issuekit_mcp_processes(runner)
    _merge_payload(payload, stop_payload)
    stopped_count = len(payload["stopped_processes"])
    payload["mcp_process_check"] = {
        "status": "checked",
        "stopped_count": stopped_count,
    }
    payload["diagnostics"].append(
        _diagnostic(
            "info",
            RELOAD_BOUNDARY_MESSAGE,
        )
    )
    if stopped_count == 0:
        payload["diagnostics"].append(_diagnostic("warn", NO_PROCESS_RELOAD_MESSAGE))
    return _finish(payload, json_output=args.json)


def verify_tool_environment(
    repo: Path,
    *,
    expect_editable: bool,
    runner: Runner | None = None,
) -> dict[str, object]:
    if runner is None:
        runner = default_runner
    payload = _empty_payload()
    tool_dir_result = runner(["uv", "tool", "dir"])
    payload["commands"].append(_command_record(tool_dir_result))
    if tool_dir_result.returncode != 0:
        payload["ok"] = False
        payload["actions"].append({"action": "verify", "ok": False})
        payload["diagnostics"].append(_diagnostic("error", "uv tool dir failed."))
        return payload

    tool_dir = Path(tool_dir_result.stdout.strip())
    env_dir = tool_dir / "issuekit"
    scripts_dir = env_dir / "Scripts"
    python_exe = scripts_dir / "python.exe"
    missing = [
        str(path)
        for path in (python_exe, scripts_dir / "issuekit.exe", scripts_dir / "issuekit-mcp.exe")
        if not path.exists()
    ]
    if missing:
        payload["ok"] = False
        payload["actions"].append({"action": "verify", "ok": False})
        for path in missing:
            payload["diagnostics"].append(_diagnostic("error", f"Missing tool environment file: {path}"))
        return payload

    import_result = runner([str(python_exe), "-c", _import_check_script()])
    payload["commands"].append(_command_record(import_result))
    if import_result.returncode != 0:
        payload["ok"] = False
        payload["actions"].append({"action": "verify", "ok": False})
        payload["diagnostics"].append(_diagnostic("error", "Tool environment import check failed."))
        return payload

    try:
        imports = json.loads(import_result.stdout)
    except json.JSONDecodeError as exc:
        payload["ok"] = False
        payload["actions"].append({"action": "verify", "ok": False})
        payload["diagnostics"].append(_diagnostic("error", f"Import check returned invalid JSON: {exc}"))
        return payload

    ok = True
    for name in ("issuekit", "issuekit.mcp.server", "mcp", "httpx"):
        record = imports.get(name, {})
        if not record.get("ok"):
            ok = False
            error = record.get("error", "not importable")
            payload["diagnostics"].append(_diagnostic("error", f"{name} is not importable: {error}"))

    if expect_editable and not _is_import_from_repo(imports.get("issuekit", {}).get("file"), repo):
        ok = False
        payload["diagnostics"].append(
            _diagnostic("error", "Global issuekit import does not resolve to the checkout path.")
        )

    if ok:
        payload["diagnostics"].append(_diagnostic("info", "Global issuekit tool environment verified."))
    else:
        payload["ok"] = False
    payload["actions"].append({"action": "verify", "ok": ok})
    return payload


def _resolve_repo_path(repo_arg: str | None) -> Path:
    repo = Path(repo_arg).expanduser() if repo_arg else Path(__file__).resolve().parents[2]
    repo = repo.resolve()
    if not (repo / "pyproject.toml").exists():
        raise ValueError(f"Repository path does not contain pyproject.toml: {repo}")
    if not (repo / "issuekit").is_dir():
        raise ValueError(f"Repository path does not contain issuekit package: {repo}")
    return repo


def _list_processes_command() -> list[str]:
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'issuekit-mcp.exe'\" | "
        "Select-Object @{Name='pid';Expression={$_.ProcessId}},"
        "@{Name='name';Expression={$_.Name}},"
        "@{Name='executable_path';Expression={$_.ExecutablePath}} | "
        "ConvertTo-Json -Compress"
    )
    return ["powershell", "-NoProfile", "-Command", script]


def _list_processes_command_posix() -> list[str]:
    return ["ps", "-A", "-o", "pid=,args="]


def _parse_ps_output(stdout: str) -> list[PosixProcess]:
    processes: list[PosixProcess] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, args = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        processes.append(PosixProcess(pid=pid, args=args))
    return processes


def _issuekit_mcp_token(args: str) -> str | None:
    # The MCP server may run either as the issuekit-mcp entry-point directly or
    # as `python .../issuekit-mcp`, so match the script token wherever it sits.
    for token in args.split():
        if PurePosixPath(token).name == MCP_PROCESS_NAME_POSIX:
            return token
    return None


def _is_issuekit_mcp_process_posix(process: PosixProcess) -> bool:
    return bool(process.args) and _issuekit_mcp_token(process.args) is not None


def _posix_process_record(process: PosixProcess) -> dict[str, object]:
    return {
        "pid": process.pid,
        "name": MCP_PROCESS_NAME_POSIX,
        "executable_path": _issuekit_mcp_token(process.args) if process.args else None,
    }


def _parse_process_json(stdout: str) -> list[WindowsProcess]:
    raw = stdout.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse process query JSON: {exc}") from exc
    items = data if isinstance(data, list) else [data]
    processes: list[WindowsProcess] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = item.get("pid") or item.get("ProcessId")
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        processes.append(
            WindowsProcess(
                pid=pid_int,
                executable_path=item.get("executable_path") or item.get("ExecutablePath"),
                name=item.get("name") or item.get("Name"),
            )
        )
    return processes


def _is_issuekit_mcp_process(process: WindowsProcess) -> bool:
    if process.name and process.name.lower() == MCP_PROCESS_NAME:
        return True
    if process.executable_path:
        return PureWindowsPath(process.executable_path).name.lower() == MCP_PROCESS_NAME
    return False


def _is_missing_tool_uninstall(result: CommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return "not installed" in text or "is not installed" in text or "no tool" in text


def _is_import_from_repo(import_file: object, repo: Path) -> bool:
    if not isinstance(import_file, str) or not import_file:
        return False
    try:
        Path(import_file).resolve().relative_to(repo.resolve())
    except ValueError:
        return False
    return True


def _import_check_script() -> str:
    return (
        "import importlib, json\n"
        "names = ['issuekit', 'issuekit.mcp.server', 'mcp', 'httpx']\n"
        "records = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        module = importlib.import_module(name)\n"
        "        records[name] = {'ok': True, 'file': getattr(module, '__file__', None)}\n"
        "    except Exception as exc:\n"
        "        records[name] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}\n"
        "print(json.dumps(records))\n"
    )


def _empty_payload() -> dict[str, object]:
    return {
        "ok": True,
        "actions": [],
        "stopped_processes": [],
        "commands": [],
        "diagnostics": [],
        "client_transport_check": {
            "status": "unsupported_from_cli",
            "message": CLIENT_TRANSPORT_UNSUPPORTED_MESSAGE,
        },
    }


def _merge_payload(target: dict[str, object], source: dict[str, object]) -> None:
    target["ok"] = bool(target["ok"]) and bool(source["ok"])
    for key in ("actions", "stopped_processes", "commands", "diagnostics"):
        target[key].extend(source[key])


def _finish(payload: dict[str, object], *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        _print_human(payload)
    return 0 if payload["ok"] else 1


def _print_human(payload: dict[str, object]) -> None:
    print("issuekit dev-tool")
    print(f"OK: {str(payload['ok']).lower()}")
    if "mcp_process_check" in payload:
        process_check = payload["mcp_process_check"]
        print(
            "MCP process check: "
            f"{process_check['status']} "
            f"(stopped_count={process_check['stopped_count']})"
        )
    transport_check = payload.get("client_transport_check")
    if isinstance(transport_check, dict):
        print(f"Client transport check: {transport_check['status']}")
    if payload["stopped_processes"]:
        print("Stopped issuekit-mcp.exe processes:")
        for process in payload["stopped_processes"]:
            path = process.get("executable_path") or "-"
            print(f"  PID {process['pid']}: {path} [{process['status']}]")
    else:
        print("Stopped issuekit-mcp.exe processes: none")
    for diagnostic in payload["diagnostics"]:
        print(f"[{diagnostic['status'].upper()}] {diagnostic['message']}")


def _command_record(result: CommandResult) -> dict[str, object]:
    return {
        "argv": list(result.argv),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _process_record(process: WindowsProcess) -> dict[str, object]:
    return {
        "pid": process.pid,
        "name": process.name,
        "executable_path": process.executable_path,
    }


def _diagnostic(status: str, message: str) -> dict[str, str]:
    return {"status": status, "message": message}


def _is_windows() -> bool:
    return platform.system() == "Windows"
