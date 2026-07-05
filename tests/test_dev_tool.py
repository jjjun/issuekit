from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

from issuekit import cli
from issuekit.commands import dev_tool


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    package = repo / "issuekit"
    package.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'issuekit'\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    return repo


def _tool_env(tmp_path: Path) -> Path:
    scripts = tmp_path / "tools" / "issuekit" / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("python.exe", "issuekit.exe", "issuekit-mcp.exe"):
        (scripts / name).write_text("", encoding="utf-8")
    return tmp_path / "tools"


def _imports_payload(repo: Path) -> str:
    return json.dumps(
        {
            "issuekit": {"ok": True, "file": str(repo / "issuekit" / "__init__.py")},
            "issuekit.mcp.server": {"ok": True, "file": str(repo / "issuekit" / "mcp" / "server.py")},
            "mcp": {"ok": True, "file": "mcp/__init__.py"},
            "httpx": {"ok": True, "file": "httpx/__init__.py"},
        }
    )


def test_builds_absolute_install_commands(tmp_path: Path) -> None:
    repo = tmp_path.resolve()

    assert dev_tool.build_editable_install_command(repo) == [
        "uv",
        "tool",
        "install",
        "--editable",
        f"{repo}[mcp]",
    ]
    assert dev_tool.build_reinstall_command(repo) == [
        "uv",
        "tool",
        "install",
        "--reinstall",
        f"issuekit[mcp] @ {repo}",
    ]


def test_install_editable_json_treats_missing_uninstall_as_nonfatal(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _repo(tmp_path)
    tool_dir = _tool_env(tmp_path)
    monkeypatch.setattr(dev_tool, "_is_windows", lambda: True)

    def runner(argv):
        argv = list(argv)
        if argv[0] == "powershell":
            return dev_tool.CommandResult(argv, 0, "")
        if argv == ["uv", "tool", "uninstall", "issuekit"]:
            return dev_tool.CommandResult(argv, 1, "", "Tool issuekit is not installed")
        if argv == dev_tool.build_editable_install_command(repo.resolve()):
            return dev_tool.CommandResult(argv, 0, "installed")
        if argv == ["uv", "tool", "dir"]:
            return dev_tool.CommandResult(argv, 0, str(tool_dir))
        if argv[0].endswith("python.exe"):
            return dev_tool.CommandResult(argv, 0, _imports_payload(repo.resolve()))
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(dev_tool, "default_runner", runner)

    exit_code = cli.main(["dev-tool", "install-editable", "--repo", str(repo), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert {"action": "uninstall", "ok": True, "non_fatal": True} in payload["actions"]
    assert any(command["argv"] == dev_tool.build_editable_install_command(repo.resolve()) for command in payload["commands"])
    assert payload["stopped_processes"] == []


def test_reinstall_fails_on_unexpected_uninstall_error(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(dev_tool, "_is_windows", lambda: True)

    def runner(argv):
        argv = list(argv)
        if argv[0] == "powershell":
            return dev_tool.CommandResult(argv, 0, "")
        if argv == ["uv", "tool", "uninstall", "issuekit"]:
            return dev_tool.CommandResult(argv, 2, "", "access denied")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(dev_tool, "default_runner", runner)

    exit_code = cli.main(["dev-tool", "reinstall", "--repo", str(repo), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert {"action": "uninstall", "ok": False} in payload["actions"]
    assert not any(command["argv"] == dev_tool.build_reinstall_command(repo.resolve()) for command in payload["commands"])


def test_reload_mcp_stops_only_issuekit_mcp_processes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dev_tool, "_is_windows", lambda: True)
    stopped: list[list[str]] = []
    process_json = json.dumps(
        [
            {
                "pid": 7,
                "name": "issuekit-mcp.exe",
                "executable_path": r"C:\Users\jj\AppData\Roaming\uv\tools\issuekit\Scripts\issuekit-mcp.exe",
            },
            {
                "pid": 8,
                "name": "python.exe",
                "executable_path": r"C:\Python312\python.exe",
            },
        ]
    )

    def runner(argv):
        argv = list(argv)
        if argv[0] == "powershell":
            return dev_tool.CommandResult(argv, 0, process_json)
        if argv[0] == "taskkill":
            stopped.append(argv)
            return dev_tool.CommandResult(argv, 0, "SUCCESS")
        raise AssertionError(f"unexpected command: {argv}")

    exit_code = dev_tool._run_reload_mcp(SimpleNamespace(json=True), runner=runner)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert stopped == [["taskkill", "/PID", "7", "/T", "/F"]]
    assert payload["stopped_processes"] == [
        {
            "pid": 7,
            "name": "issuekit-mcp.exe",
            "executable_path": r"C:\Users\jj\AppData\Roaming\uv\tools\issuekit\Scripts\issuekit-mcp.exe",
            "status": "stopped",
        }
    ]
    assert payload["mcp_process_check"] == {"status": "checked", "stopped_count": 1}
    assert payload["client_transport_check"]["status"] == "unsupported_from_cli"


def test_reload_mcp_stops_only_issuekit_mcp_processes_posix(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dev_tool, "_is_windows", lambda: False)
    stopped: list[list[str]] = []
    ps_output = "\n".join(
        [
            "  7 /home/jj/.local/share/uv/tools/issuekit/bin/python3 /home/jj/.local/bin/issuekit-mcp",
            "  8 /usr/bin/python3 /home/jj/projects/issuekit/.venv/bin/issuekit dev-tool reload-mcp",
            "  9 /usr/bin/python3 -m http.server",
        ]
    )

    def runner(argv):
        argv = list(argv)
        if argv[0] == "ps":
            return dev_tool.CommandResult(argv, 0, ps_output)
        if argv[0] == "kill":
            stopped.append(argv)
            return dev_tool.CommandResult(argv, 0, "")
        raise AssertionError(f"unexpected command: {argv}")

    exit_code = dev_tool._run_reload_mcp(SimpleNamespace(json=True), runner=runner)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert stopped == [["kill", "-TERM", "7"]]
    assert payload["stopped_processes"] == [
        {
            "pid": 7,
            "name": "issuekit-mcp",
            "executable_path": "/home/jj/.local/bin/issuekit-mcp",
            "status": "stopped",
        }
    ]
    assert payload["mcp_process_check"] == {"status": "checked", "stopped_count": 1}
    assert payload["client_transport_check"]["status"] == "unsupported_from_cli"


def test_reload_mcp_warns_when_no_processes_stopped(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dev_tool, "_is_windows", lambda: True)

    def runner(argv):
        argv = list(argv)
        if argv[0] == "powershell":
            return dev_tool.CommandResult(argv, 0, "")
        raise AssertionError(f"unexpected command: {argv}")

    exit_code = dev_tool._run_reload_mcp(SimpleNamespace(json=True), runner=runner)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["stopped_processes"] == []
    assert payload["mcp_process_check"] == {"status": "checked", "stopped_count": 0}
    assert payload["client_transport_check"]["status"] == "unsupported_from_cli"
    assert any(
        diagnostic["status"] == "warn"
        and "does not reconnect an already-open Codex or Claude stdio transport"
        in diagnostic["message"]
        for diagnostic in payload["diagnostics"]
    )


def test_reload_mcp_human_output_explains_transport_boundary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dev_tool, "_is_windows", lambda: True)

    def runner(argv):
        argv = list(argv)
        if argv[0] == "powershell":
            return dev_tool.CommandResult(argv, 0, "")
        raise AssertionError(f"unexpected command: {argv}")

    exit_code = dev_tool._run_reload_mcp(SimpleNamespace(json=False), runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MCP process check: checked (stopped_count=0)" in captured.out
    assert "Client transport check: unsupported_from_cli" in captured.out
    assert "reload or restart the MCP client session" in captured.out


def test_filter_issuekit_mcp_processes_posix_matches_script_token() -> None:
    processes = [
        dev_tool.PosixProcess(1, "/usr/bin/python3 -m http.server"),
        dev_tool.PosixProcess(2, "/home/jj/.local/share/uv/tools/issuekit/bin/issuekit-mcp"),
        dev_tool.PosixProcess(3, "/usr/bin/python3 /home/jj/.local/bin/issuekit-mcp"),
        dev_tool.PosixProcess(4, "/home/jj/.venv/bin/issuekit dev-tool reload-mcp"),
    ]

    assert dev_tool.filter_issuekit_mcp_processes_posix(processes) == processes[1:3]


def test_filter_issuekit_mcp_processes_matches_name_or_executable() -> None:
    processes = [
        dev_tool.WindowsProcess(1, name="python.exe", executable_path=r"C:\Python312\python.exe"),
        dev_tool.WindowsProcess(2, name="issuekit-mcp.exe"),
        dev_tool.WindowsProcess(3, executable_path=r"C:\tools\issuekit-mcp.exe"),
    ]

    assert dev_tool.filter_issuekit_mcp_processes(processes) == processes[1:]
