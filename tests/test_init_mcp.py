from pathlib import Path

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
    assert "issuekit protocol --agent codex" in agents
    assert "issuekit protocol --agent claude" in claude
    assert "claim_next_task" not in agents
    assert "next_review" not in claude


def test_init_with_mcp_is_idempotent(tmp_path: Path) -> None:
    init_repo(tmp_path, with_mcp=True)
    init_repo(tmp_path, with_mcp=True)

    codex_config = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

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


def test_init_with_mcp_preserves_existing_mcp_json_without_force(tmp_path: Path) -> None:
    custom = '{"mcpServers": {}}\n'
    (tmp_path / ".mcp.json").write_text(custom, encoding="utf-8")

    init_repo(tmp_path, with_mcp=True)

    assert (tmp_path / ".mcp.json").read_text(encoding="utf-8") == custom


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
