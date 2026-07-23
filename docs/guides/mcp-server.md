# MCP server

Install or upgrade the MCP server once as a global tool:

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

When upgrading a global tool, stop running issuekit MCP servers first. MCP
clients hold `issuekit-mcp.exe` while the session is running, so replacing the
tool underneath them can leave the install half-removed. Normal users should
prefer:

```powershell
uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"
```

Use an absolute path or URL, not a bare `.`, to avoid cwd-dependent installs.
After any reinstall or upgrade, restart running codex and Claude Code sessions;
they keep a stdio connection to the old server process.

## Developer commands

Issuekit developers working from a Windows checkout should use the repeatable
developer commands instead:

```powershell
uv run issuekit dev-tool install-editable
uv run issuekit dev-tool reload-mcp
uv run issuekit dev-tool reinstall
```

`install-editable` reflects source edits the next time a global `issuekit` or
`issuekit-mcp` process starts. `reload-mcp` stops only matching
`issuekit-mcp.exe` processes and reports their PIDs and executable paths when
available. It cannot respawn or reconnect an already-open codex or Claude Code
stdio MCP transport; the MCP client owns that connection. If MCP tools still
return `Transport closed` after `reload-mcp`, reload or restart the MCP client
session, reload the thread/window if supported, or start a fresh session so the
client can spawn a new `issuekit-mcp` transport. `reinstall` is the recovery
path when editable metadata gets stale or a global tool environment is
partially broken. All generated uv install commands use an absolute checkout
path, never a bare `.`.

## Per-repo scaffolding

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

Orchestrators that only need a preflight should use the read-only check:

```powershell
issuekit setup check --json
```

The check does not write files or run subprocesses. Its JSON object reports
`ok`, `needs_setup`, `would_write`, `would_update`, `client_transport_check`,
`diagnostics`, and `actions` so automation can decide whether to run the
applying command. `client_transport_check.status` is `unsupported_from_cli`
because a standalone CLI can verify static readiness but cannot prove that an
already-open codex or Claude Code stdio transport is live. `issuekit setup`
keeps its applying behavior, and `issuekit setup apply --json` is an explicit
alias for that path.

The repo scaffold writes `.mcp.json`, appends `.codex/config.toml` when needed,
and adds thin handoff references to `AGENTS.md` and `CLAUDE.md`. The generated
MCP entries run the global `issuekit-mcp` binary; they do not use `uv run`, so
they work outside the issuekit checkout. Launch codex or Claude Code from the
target repo root so the server resolves repo configuration.

## Health and troubleshooting

When the MCP transport is live, the MCP `health` tool reports the server cwd,
issuekit version, resolved project, API URL presence, local worker presence,
and author guard state without mutating issue lifecycle state.

If an MCP client still exposes `mcp__issuekit` tools but every call fails with
`Transport closed`, tool discovery is stale. Until the client transport is
reloaded, use the equivalent CLI commands for read-only inspection and proposal
inbox work:

```powershell
issuekit protocol --role author
issuekit incoming --json
issuekit info --json
```

Repo-local `.env` files are treated as trusted repository input only for
`ISSUEKIT_*` keys. Sensitive API settings loaded from `.env` are announced on
stderr so credential redirection is visible.

For local development, install the optional MCP group and start the stdio server
from a checkout with:

```powershell
uv run --group mcp issuekit-mcp
```

## MCP boundary

The MCP surface is for tracker reads and state changes performed by the calling
agent. Commands that launch other agents remain CLI orchestration: `implement`,
`review`, `serve`, `triage`, `request`, and `negotiate`. In particular,
negotiation can hold a stdio transport open for several agent turns, so running
it from an MCP agent session makes a fragile transport failure more likely.

`run_proposal_checks` predates this boundary. It was added under the
mirror-the-CLI instruction in issuekit#158, which issuekit#295 has replaced.
The tool is deprecated and retained temporarily for client compatibility;
use `issuekit serve --proposal-checks --proposal-check-limit <n>` or
`issuekit proposal-checks --agent <a> --once` instead. It will be removed once
the CLI workflow has been proven in use. It is not precedent for exposing more
agent-launching orchestration through MCP. Read-only negotiation thread
inspection is available through `list_negotiation_threads`.
