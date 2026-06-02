# issuekit

`issuekit` is a language-neutral CLI for sharing the `docs/issues/` local issue
tracker convention across repositories. It consolidates the original
mine-js-monorepo Node scripts into a Python package that can be installed once
and reused from each repo.

## Install

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

Install with the `mcp` extra when codex or Claude Code will use the handoff MCP
server. Without the extra, `issuekit-mcp` cannot start.

For local development:

```powershell
uv sync
uv run issuekit --help
```

## MCP server

Install or upgrade the MCP server once as a global tool:

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

When upgrading a global tool, stop running issuekit MCP servers first. MCP
clients hold `issuekit-mcp.exe` while the session is running, so replacing the
tool underneath them can leave the install half-removed. Prefer:

```powershell
uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"
```

Use an absolute path or URL, not a bare `.`, to avoid cwd-dependent installs.
After any reinstall or upgrade, restart running codex and Claude Code sessions;
they keep a stdio connection to the old server process.

Then scaffold each repository that uses issuekit:

```powershell
issuekit setup
```

This runs `init --with-mcp`, prints setup diagnostics, and shows the optional
global codex MCP-store command:

```powershell
codex mcp add issuekit -- issuekit-mcp
```

That global command is unnecessary when codex reads the repo's
`.codex/config.toml`, but it is useful for users who manage MCP servers through
the global codex store. `issuekit setup` only edits files inside the current
repo. It never kills processes and never edits global codex config.

Automation should use the stable JSON contract:

```powershell
issuekit setup --json
```

This still scaffolds the repo, but prints one JSON object with `ok`, `scaffold`,
and `diagnostics` fields instead of the human checklist. `ok: false` means at
least one diagnostic still needs action, often an optional global install or
configuration step; the command still exits 0 when repo scaffolding succeeds.

The repo scaffold writes `.mcp.json`, appends `.codex/config.toml` when needed,
and adds thin handoff references to `AGENTS.md` and `CLAUDE.md`. The generated
MCP entries run the global `issuekit-mcp` binary; they do not use `uv run`, so
they work outside the issuekit checkout. Launch codex or Claude Code from the
target repo root so the server resolves the correct `docs/issues/` directory.

For local development, install the optional MCP group and start the stdio server
from a checkout with:

```powershell
uv run --group mcp issuekit-mcp
```

## Handoff protocol

The two-agent protocol is centralized in issuekit:

```powershell
issuekit protocol
issuekit protocol --agent codex
issuekit protocol --agent claude
```

The MCP server exposes the same text as its instructions and through the
`get_protocol` tool. Consuming repos should reference this command instead of
copying the steps.

## Commands

| Command | Purpose |
|---------|---------|
| `issuekit info [--json]` | Show tracker status and the next issue id. |
| `issuekit validate` | Check filenames, ids, frontmatter, indexes, mojibake, and ASCII rules. |
| `issuekit generate-indexes` | Regenerate `docs/issues/indexes/*`. |
| `issuekit complete <id> --summary "..." --verification "..."` | Move active issue to completed, regenerate indexes, and validate. |
| `issuekit claim --assignee codex` | Claim the next active issue for an implementer. |
| `issuekit submit-review <id> --summary "..." [--assignee codex] [--reviewer claude]` | Submit implemented work to a reviewer. |
| `issuekit request-changes <id> --notes "..." [--assignee codex] [--reviewer claude]` | Return a reviewed issue to implementation. |
| `issuekit queue --assignee claude [--stage review]` | List active issues for an assignee. |
| `issuekit check-encoding [--json]` | Check tracked source files for leading BOM bytes and likely mojibake. |
| `issuekit protocol [--agent codex\|claude]` | Print the canonical handoff protocol. |
| `issuekit init [--with-mcp]` | Install tracker templates, encoding hooks, and optional MCP handoff scaffolding. |
| `issuekit setup [--force] [--json]` | Run per-repo MCP handoff scaffolding and setup diagnostics. |

The issue file specification lives in `docs/issues/README.md`.

## Configuration

Python repositories can configure issuekit in `pyproject.toml`:

```toml
[tool.issuekit]
issues_dir = "docs/issues"
ascii_id_threshold = 407
recent_count = 30
assignees = ["codex", "claude"]
stages = ["todo", "implementing", "review", "changes_requested", "done"]
default_reviewer = "claude"
```

Non-Python repositories can use a standalone `issuekit.toml` at the repo root
with the same keys at the top level:

```toml
issues_dir = "docs/issues"
ascii_id_threshold = 407
recent_count = 30
assignees = ["codex", "claude"]
stages = ["todo", "implementing", "review", "changes_requested", "done"]
default_reviewer = "claude"
```

When both files exist, `[tool.issuekit]` in `pyproject.toml` takes precedence.
If `pyproject.toml` exists without `[tool.issuekit]`, issuekit falls back to
`issuekit.toml`.

`default_reviewer` controls where MCP and CLI review handoffs go when no
reviewer is specified. It must be one of the configured `assignees`.

## Development

This repo dogfoods issuekit. Implementation tasks live in `docs/issues/active/`
as codex-ready issues.
