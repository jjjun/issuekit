from pathlib import Path
import json

from issuekit.commands.init import init_repo


def test_init_default_does_not_write_mcp_scaffold(tmp_path: Path) -> None:
    init_repo(tmp_path)

    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_init_with_mcp_writes_global_binary_scaffold(tmp_path: Path) -> None:
    result = init_repo(tmp_path, with_mcp=True)

    mcp_json = (tmp_path / ".mcp.json").read_text(encoding="utf-8")
    codex_config = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    assert ".mcp.json" in result.written
    assert ".codex/config.toml" in result.written
    assert '"command": "issuekit-mcp"' in mcp_json
    assert 'command = "issuekit-mcp"' in codex_config
    assert "uv" not in mcp_json
    assert "uv run" not in codex_config
    assert "issuekit protocol --agent" in agents
    assert "issuekit protocol --agent" in claude
    assert "claim_next_task" not in agents
    assert "next_review" not in claude


def test_init_with_mcp_is_idempotent(tmp_path: Path) -> None:
    init_repo(tmp_path, with_mcp=True)
    first_mcp_json = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    init_repo(tmp_path, with_mcp=True)

    mcp_json = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    codex_config = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    assert mcp_json == first_mcp_json
    assert list(mcp_json["mcpServers"]).count("issuekit") == 1
    assert codex_config.count("[mcp_servers.issuekit]") == 1
    assert agents.count("## Handoff protocol") == 1
    assert claude.count("## Handoff protocol") == 1


def test_init_with_mcp_appends_to_existing_files(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("[other]\nvalue = true\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nKeep this.\n", encoding="utf-8")

    init_repo(tmp_path, with_mcp=True)

    codex_config = (codex_dir / "config.toml").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    assert "[other]\nvalue = true\n" in codex_config
    assert codex_config.count("[mcp_servers.issuekit]") == 1
    assert agents.startswith("# Agents\n\nKeep this.\n")
    assert agents.count("## Handoff protocol") == 1
    assert claude.count("## Handoff protocol") == 1


def test_init_with_mcp_merges_existing_mcp_json_without_force(tmp_path: Path) -> None:
    existing = {"mcpServers": {"other": {"command": "x"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    init_repo(tmp_path, with_mcp=True)

    merged = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert merged["mcpServers"]["other"] == {"command": "x"}
    assert merged["mcpServers"]["issuekit"] == {"command": "issuekit-mcp", "args": []}


def test_init_with_mcp_adds_mcp_servers_object_when_missing(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{}\n", encoding="utf-8")

    init_repo(tmp_path, with_mcp=True)

    merged = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert merged == {"mcpServers": {"issuekit": {"command": "issuekit-mcp", "args": []}}}


def test_init_with_mcp_skips_existing_issuekit_server(tmp_path: Path) -> None:
    existing = {"mcpServers": {"issuekit": {"command": "custom"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    result = init_repo(tmp_path, with_mcp=True)

    assert json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8")) == existing
    assert ".mcp.json" in result.skipped


def test_init_with_mcp_force_overwrites_mcp_json(tmp_path: Path) -> None:
    existing = {"mcpServers": {"other": {"command": "x"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    init_repo(tmp_path, with_mcp=True, force=True)

    overwritten = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert overwritten == {"mcpServers": {"issuekit": {"command": "issuekit-mcp", "args": []}}}


def test_init_with_mcp_guides_for_malformed_mcp_json(tmp_path: Path) -> None:
    original = "{not json\n"
    (tmp_path / ".mcp.json").write_text(original, encoding="utf-8")

    result = init_repo(tmp_path, with_mcp=True)

    assert (tmp_path / ".mcp.json").read_text(encoding="utf-8") == original
    assert ".mcp.json" in result.skipped
    assert any("Add this issuekit server manually" in item for item in result.guidance)
    assert any('"command": "issuekit-mcp"' in item for item in result.guidance)


def test_init_with_mcp_written_files_are_ascii_lf_without_bom(tmp_path: Path) -> None:
    init_repo(tmp_path, with_mcp=True)

    for path in [
        tmp_path / ".mcp.json",
        tmp_path / ".codex" / "config.toml",
        tmp_path / "AGENTS.md",
        tmp_path / "CLAUDE.md",
    ]:
        content = path.read_bytes()
        assert not content.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in content
        content.decode("ascii")
