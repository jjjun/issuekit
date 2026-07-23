# Installation

## Global tool

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

Install with the `mcp` extra when codex or Claude Code will use the handoff MCP
server. Without the extra, `issuekit-mcp` cannot start.

## Local development

```powershell
uv sync
uv run issuekit --help
uv run issuekit dev-tool install-editable
```

On Windows, `dev-tool install-editable` installs the global `issuekit` and
`issuekit-mcp` tool shims from this checkout in editable mode. It stops stale
`issuekit-mcp.exe` processes first, uninstalls any existing global `issuekit`
tool if present, installs with the `mcp` extra, and verifies the resulting tool
environment.

## Next steps

- [MCP server](mcp-server.md) to scaffold a repository for handoff work.
- [Configuration](configuration.md) to point issuekit at an API project.
- [Testing](testing.md) to run the project gates.
