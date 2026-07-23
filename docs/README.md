# issuekit documentation

This directory holds the long-form issuekit documentation. The repository
[`README.md`](../README.md) stays short: it covers what issuekit is, how to
install it, and where to go next.

## Layout

| Directory | Audience | Purpose |
|-----------|----------|---------|
| [`guides/`](guides) | Humans and agents | How to install, configure, and operate issuekit. Reviewed like code. |
| [`agent-notes/`](agent-notes) | Agents | Operational memory agents read and write freely while working in this repo. |

## Guides

| Guide | Contents |
|-------|----------|
| [Installation](guides/installation.md) | Global tool install, editable development install, Windows tool shims. |
| [MCP server](guides/mcp-server.md) | Installing, scaffolding, and troubleshooting the handoff MCP server. |
| [Handoff protocol](guides/handoff-protocol.md) | Where the canonical author/implementer/reviewer protocol text lives. |
| [Commands](guides/commands.md) | Full CLI command reference. |
| [Configuration](guides/configuration.md) | Config file layers, precedence, agent overlays, reviewer and implementer policy. |
| [Cross-project proposals](guides/cross-project-proposals.md) | Proposal inboxes, dependencies, refs, adoption and reply flow. |
| [Cross-project negotiation](guides/negotiation.md) | Bounded agent conversations that settle a shared contract before implementation. |
| [PM request router](guides/pm-request.md) | Route natural-language development requests to project proposal inboxes. |
| [Directed addressing](guides/directed-addressing.md) | Repo, worker, and agent axes; `worker.repo@machine` targets. |
| [Registry maintenance](guides/registry-maintenance.md) | Removing and pruning stale workers and repo catalog entries. |
| [Orphaned claim detection](guides/orphaned-claim-detection.md) | Finding and reclaiming stalled implementing claims. |
| [Separation-of-duties guards](guides/separation-of-duties.md) | The four guards, their error strings, and recovery paths. |
| [Testing](guides/testing.md) | Local gates, live contract tests, CI workflows. |
| [Development](guides/development.md) | Dogfooding workflow and Windows developer commands. |

## What does not live here

- The handoff protocol text itself. `issuekit protocol` and the MCP
  `get_protocol` tool are the source of truth; see
  [guides/handoff-protocol.md](guides/handoff-protocol.md).
- The agent runtime boundary, which stays next to its code in
  [`issuekit/agentrun/README.md`](../issuekit/agentrun/README.md).
- The project profile in [`ISSUEKIT.md`](../ISSUEKIT.md).
