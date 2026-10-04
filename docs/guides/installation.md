# Installation

The project requires Python 3.12 or newer, `uv`, and Git 2.24 or newer.

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
```

On Windows, `dev-tool install-editable` installs the global `issuekit` and
`issuekit-mcp` tool shims from this checkout in editable mode:

```powershell
uv run issuekit dev-tool install-editable
```

It stops stale `issuekit-mcp.exe` processes first, uninstalls any existing
global `issuekit` tool if present, installs with the `mcp` extra, and verifies
the resulting tool environment. On POSIX, install the editable checkout with:

```sh
uv tool install --editable "<abs-checkout>[mcp]"
```

## Next steps

- [MCP server](mcp-server.md) to scaffold a repository for handoff work.
- [Configuration](configuration.md) to point issuekit at an API project.
- [Testing](testing.md) to run the project gates.
