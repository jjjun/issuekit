from pathlib import Path
import json
import subprocess

from issuekit import cli
from issuekit.commands.init import init_repo
from issuekit.commands import setup


def _force_mcp_available(monkeypatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: "C:/tools/issuekit-mcp.exe")
    monkeypatch.setattr(setup, "import_module", lambda _name: object())


def _diagnostic_status(diagnostics: list[setup.Diagnostic], label: str) -> str:
    for diagnostic in diagnostics:
        if diagnostic.label == label:
            return diagnostic.status
    raise AssertionError(f"missing diagnostic: {label}")


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_setup_empty_repo_scaffolds_mcp_and_prints_checklist(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Wrote: .mcp.json" in captured.out
    assert "Setup diagnostics:" in captured.out
    assert "[OK] .mcp.json contains an issuekit MCP server." in captured.out
    assert "[OK] .codex/config.toml contains [mcp_servers.issuekit]." in captured.out
    assert (tmp_path / ".mcp.json").exists()
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "docs" / "issues" / "incoming" / ".gitkeep").exists()
    assert cli.main(["migrate-to-api", "--dry-run"]) == 0


def test_setup_reports_missing_and_present_mcp_json_issuekit_server(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}}) + "\n",
        encoding="utf-8",
    )

    diagnostics = setup.collect_diagnostics(tmp_path)

    assert (
        _diagnostic_status(diagnostics, ".mcp.json does not contain an issuekit MCP server.")
        == "ACTION"
    )

    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"issuekit": {"command": "issuekit-mcp", "args": []}}})
        + "\n",
        encoding="utf-8",
    )

    diagnostics = setup.collect_diagnostics(tmp_path)

    assert _diagnostic_status(diagnostics, ".mcp.json contains an issuekit MCP server.") == "OK"


def test_setup_reports_present_and_absent_codex_config(tmp_path: Path) -> None:
    diagnostics = setup.collect_diagnostics(tmp_path)

    assert _diagnostic_status(diagnostics, ".codex/config.toml is missing.") == "ACTION"

    codex_config = tmp_path / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text(
        "[mcp_servers.issuekit]\ncommand = \"issuekit-mcp\"\nargs = []\n",
        encoding="utf-8",
    )

    diagnostics = setup.collect_diagnostics(tmp_path)

    assert _diagnostic_status(diagnostics, ".codex/config.toml contains [mcp_servers.issuekit].") == "OK"


def test_setup_prints_codex_mcp_add_guidance_without_running_process(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    def fail_process(*_args, **_kwargs):
        raise AssertionError("setup must not run subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_process)
    monkeypatch.setattr(subprocess, "Popen", fail_process)

    exit_code = cli.main(["setup"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "codex mcp add issuekit -- issuekit-mcp" in captured.out


def test_setup_output_is_ascii(tmp_path: Path, monkeypatch, capsys) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup"])

    captured = capsys.readouterr()
    assert exit_code == 0
    captured.out.encode("ascii")


def test_setup_check_json_current_repo_reports_ok_without_writes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    init_repo(tmp_path, with_mcp=True)
    before = _file_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "check", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["state"] == "current"
    assert payload["needs_setup"] is False
    assert payload["would_write"] is False
    assert payload["would_update"] is False
    assert payload["actions"] == []
    assert _file_snapshot(tmp_path) == before


def test_setup_check_json_missing_repo_reports_writes_without_writing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    def fail_process(*_args, **_kwargs):
        raise AssertionError("setup check must not run subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_process)
    monkeypatch.setattr(subprocess, "Popen", fail_process)

    exit_code = cli.main(["setup", "check", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["state"] == "missing"
    assert payload["needs_setup"] is True
    assert payload["would_write"] is True
    assert payload["would_update"] is False
    assert ".mcp.json" in {item["path"] for item in payload["actions"]}
    assert _file_snapshot(tmp_path) == {}


def test_setup_check_json_stale_repo_reports_updates_without_writing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    init_repo(tmp_path, with_mcp=True)
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8", newline="\n")
    before = _file_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "check", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["state"] == "stale"
    assert payload["needs_setup"] is True
    assert payload["would_write"] is False
    assert payload["would_update"] is True
    paths = {item["path"] for item in payload["actions"]}
    assert "AGENTS.md" in paths
    assert "docs/issues/indexes/active.md" not in paths
    assert _file_snapshot(tmp_path) == before


def test_setup_check_json_blocked_repo_reports_manual_action_without_writing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    init_repo(tmp_path, with_mcp=True)
    (tmp_path / ".mcp.json").write_text("{not-json\n", encoding="utf-8", newline="\n")
    before = _file_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "--check", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["state"] == "blocked"
    assert payload["needs_setup"] is True
    assert payload["would_write"] is False
    assert payload["would_update"] is False
    assert payload["actions"] == [
        {
            "path": ".mcp.json",
            "state": "blocked",
            "action": "manual",
            "reason": "invalid JSON blocks issuekit setup from safely merging this file.",
        }
    ]
    assert _file_snapshot(tmp_path) == before


def test_setup_reports_missing_mcp_extra(monkeypatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)

    def fail_import(_name: str):
        raise ModuleNotFoundError("No module named 'mcp'")

    monkeypatch.setattr(setup, "import_module", fail_import)

    diagnostics = setup.collect_diagnostics(Path.cwd())

    assert _diagnostic_status(diagnostics, "issuekit-mcp command is not on PATH.") == "ACTION"
    assert (
        _diagnostic_status(diagnostics, "issuekit MCP server dependencies are not importable.")
        == "ACTION"
    )


def test_setup_json_empty_repo_prints_valid_payload(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert isinstance(payload["ok"], bool)
    assert ".mcp.json" in payload["scaffold"]["written"]
    assert isinstance(payload["diagnostics"], list)
    assert payload["diagnostics"]
    assert all({"status", "label", "details"} == set(item) for item in payload["diagnostics"])


def test_setup_json_ok_true_when_all_diagnostics_ok(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert {item["status"] for item in payload["diagnostics"]} == {"OK"}


def test_setup_json_ok_false_when_diagnostic_needs_action(tmp_path: Path, monkeypatch) -> None:
    init_result = init_repo(tmp_path, with_mcp=True)
    codex_config = tmp_path / ".codex" / "config.toml"
    codex_config.unlink()
    _force_mcp_available(monkeypatch)

    payload = setup.build_json_payload(tmp_path, init_result)

    assert payload["ok"] is False
    assert any(item["status"] == "ACTION" for item in payload["diagnostics"])


def test_setup_json_diagnostics_match_collect_diagnostics(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "--json"])

    payload = json.loads(capsys.readouterr().out)
    diagnostics = setup.collect_diagnostics(tmp_path)
    assert exit_code == 0
    assert [
        (item["status"], item["label"], item["details"]) for item in payload["diagnostics"]
    ] == [
        (diagnostic.status, diagnostic.label, list(diagnostic.details))
        for diagnostic in diagnostics
    ]


def test_setup_json_output_is_ascii(tmp_path: Path, monkeypatch, capsys) -> None:
    _force_mcp_available(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["setup", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    captured.out.encode("ascii")
