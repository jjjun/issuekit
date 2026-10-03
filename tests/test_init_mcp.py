import json
import tomllib
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
    for name in (
        "ISSUEKIT_API_URL",
        "ISSUEKIT_PROJECT",
        "ISSUEKIT_CONFIG",
        "ISSUEKIT_TOKEN_CACHE",
        "ISSUEKIT_ALLOW_INSECURE",
        "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
        "XDG_CONFIG_HOME",
    ):
        assert f'"{name}"' in codex_config
    assert "ISSUEKIT_API_TOKEN" not in codex_config
    assert "uv" not in mcp_json
    assert "uv run" not in codex_config
    assert "issuekit protocol --role" in agents
    assert "issuekit protocol --agent" in agents
    assert "@AGENTS.md" in claude
    assert "## Handoff protocol" not in claude
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
    assert claude == "@AGENTS.md\n"


def test_init_with_mcp_appends_to_existing_files(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("[other]\nvalue = true\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nKeep this.\n", encoding="utf-8")
    original_claude = "# Claude guidance\n\nKeep this.\n"
    (tmp_path / "CLAUDE.md").write_text(original_claude, encoding="utf-8")

    result = init_repo(tmp_path, with_mcp=True)

    codex_config = (codex_dir / "config.toml").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    assert "[other]\nvalue = true\n" in codex_config
    assert codex_config.count("[mcp_servers.issuekit]") == 1
    assert agents.startswith("# Agents\n\nKeep this.\n")
    assert agents.count("## Handoff protocol") == 1
    assert claude == original_claude
    assert "CLAUDE.md does not import AGENTS.md" in "\n".join(result.guidance)


def test_init_with_mcp_leaves_existing_agents_import_untouched(tmp_path: Path) -> None:
    claude_path = tmp_path / "CLAUDE.md"
    original = "@AGENTS.md\n"
    claude_path.write_text(original, encoding="utf-8", newline="\n")

    result = init_repo(tmp_path, with_mcp=True)

    assert claude_path.read_bytes() == original.encode("ascii")
    assert "CLAUDE.md" in result.skipped
    assert "CLAUDE.md" not in result.written
    assert not any("CLAUDE.md does not import" in item for item in result.guidance)


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


def test_init_with_mcp_force_refreshes_only_issuekit_mcp_json_entry(tmp_path: Path) -> None:
    existing = {
        "name": "workspace",
        "mcpServers": {
            "other": {"command": "x"},
            "issuekit": {"command": "custom"},
        },
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    init_repo(tmp_path, with_mcp=True, force=True)

    refreshed = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert refreshed["name"] == "workspace"
    assert refreshed["mcpServers"]["other"] == {"command": "x"}
    assert refreshed["mcpServers"]["issuekit"] == {"command": "issuekit-mcp", "args": []}


def test_init_with_mcp_force_refreshes_only_standard_codex_server_table(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        'model = "gpt-test"\n'
        '[mcp_servers.other]\n'
        'command = "other-mcp"\n'
        '[mcp_servers.issuekit]\n'
        'command = "custom-mcp"\n'
        'args = ["custom"]\n',
        encoding="utf-8",
    )

    init_repo(tmp_path, with_mcp=True, force=True)

    refreshed_text = config_path.read_text(encoding="utf-8")
    refreshed = tomllib.loads(refreshed_text)
    assert refreshed["model"] == "gpt-test"
    assert refreshed["mcp_servers"]["other"] == {"command": "other-mcp"}
    assert refreshed["mcp_servers"]["issuekit"]["command"] == "issuekit-mcp"
    assert "env_vars" in refreshed["mcp_servers"]["issuekit"]


def test_init_with_mcp_force_leaves_quoted_and_inline_codex_entries_parseable(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    originals = (
        '[mcp_servers."issuekit"]\ncommand = "custom"\n',
        'mcp_servers = { issuekit = { command = "custom" } }\n',
    )
    for original in originals:
        config_path.write_text(original, encoding="utf-8")

        result = init_repo(tmp_path, with_mcp=True, force=True)

        assert config_path.read_text(encoding="utf-8") == original
        assert tomllib.loads(original)["mcp_servers"]["issuekit"]["command"] == "custom"
        assert ".codex/config.toml" in result.skipped
        assert any(
            "Add or refresh this issuekit server manually" in item for item in result.guidance
        )


def test_init_with_mcp_leaves_quoted_codex_entry_unchanged_with_guidance(
    tmp_path: Path,
) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    original = '[mcp_servers."issuekit"]\ncommand = "custom"\n'
    config_path.write_text(original, encoding="utf-8")

    result = init_repo(tmp_path, with_mcp=True)

    assert config_path.read_text(encoding="utf-8") == original
    assert tomllib.loads(original)["mcp_servers"]["issuekit"]["command"] == "custom"
    assert ".codex/config.toml" in result.skipped
    assert result.guidance


def test_init_with_mcp_comment_header_does_not_count_as_codex_server(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text("# [mcp_servers.issuekit]\nmodel = \"gpt-test\"\n", encoding="utf-8")

    init_repo(tmp_path, with_mcp=True)

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-test"
    assert parsed["mcp_servers"]["issuekit"]["command"] == "issuekit-mcp"


def test_init_with_mcp_adds_env_vars_to_existing_codex_table(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        '[mcp_servers.issuekit]\ncommand = "issuekit-mcp"\nargs = []\n',
        encoding="utf-8",
    )

    result = init_repo(tmp_path, with_mcp=True)

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["issuekit"]["env_vars"]
    assert ".codex/config.toml" in result.written


def test_init_with_mcp_does_not_write_malformed_codex_config(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    original = "[mcp_servers.issuekit\n"
    config_path.write_text(original, encoding="utf-8")

    result = init_repo(tmp_path, with_mcp=True, force=True)

    assert config_path.read_text(encoding="utf-8") == original
    assert ".codex/config.toml" in result.skipped
    assert result.guidance


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
