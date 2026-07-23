# issuekit

`issuekit` is a language-neutral CLI for sharing an API-backed issue handoff
workflow across repositories. Issue lifecycle state and cross-project proposals
live in a mine-py API project.

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
uv run issuekit dev-tool install-editable
```

See [docs/guides/installation.md](docs/guides/installation.md) for details.

## Quick start

Scaffold a repository for handoff work, then register the checkout:

```powershell
issuekit setup
issuekit add
```

Print the canonical handoff protocol for your role:

```powershell
issuekit protocol --role implementer
```

## Documentation

Full documentation lives in [`docs/`](docs).

| Guide | Contents |
|-------|----------|
| [Installation](docs/guides/installation.md) | Global tool install, editable development install, Windows tool shims. |
| [MCP server](docs/guides/mcp-server.md) | Installing, scaffolding, and troubleshooting the handoff MCP server. |
| [Handoff protocol](docs/guides/handoff-protocol.md) | Where the canonical author/implementer/reviewer protocol text lives. |
| [Commands](docs/guides/commands.md) | Full CLI command reference. |
| [Configuration](docs/guides/configuration.md) | Config file layers, precedence, agent overlays, reviewer and implementer policy. |
| [Cross-project proposals](docs/guides/cross-project-proposals.md) | Proposal inboxes, dependencies, refs, adoption and reply flow. |
| [Directed addressing](docs/guides/directed-addressing.md) | Repo, worker, and agent axes; `worker.repo@machine` targets. |
| [Registry maintenance](docs/guides/registry-maintenance.md) | Removing and pruning stale workers and repo catalog entries. |
| [Orphaned claim detection](docs/guides/orphaned-claim-detection.md) | Finding and reclaiming stalled implementing claims. |
| [Separation-of-duties guards](docs/guides/separation-of-duties.md) | The four guards, their error strings, and recovery paths. |
| [Testing](docs/guides/testing.md) | Local gates, live contract tests, CI workflows. |
| [Development](docs/guides/development.md) | Dogfooding workflow and Windows developer commands. |

Agents working in this repo also keep operational memory in
[`docs/agent-notes/`](docs/agent-notes).

## Tests

```powershell
uv run pytest
uv run issuekit check-encoding
```

See [docs/guides/testing.md](docs/guides/testing.md) for live contract tests and
the manual CI workflow.
