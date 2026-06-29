# CLAUDE.md

## Handoff protocol

This repo uses the issuekit multi-agent handoff. For the current steps, run
`issuekit protocol --agent <agent>` (e.g. `codex`, `claude`, or `kimi`) or
`issuekit protocol --role <role>` (e.g. `implementer` or `reviewer`), or read
the issuekit MCP server instructions / `get_protocol` tool.

Do not copy the steps here; issuekit is the source of truth. Launch your agent
from the repo root so the MCP server resolves the repo configuration.

## Project notes

- Authors write proposals and implementation-ready issues, then stop.
- Implementers claim active issues from the configured API project.
- Reviewers decide submitted issues and may also be the original author when a
  different implementer did the work.
- If the proposal-system MCP tools hang or error, use the equivalent CLI with
  `--json` (`issuekit propose/incoming/adopt`); they share one implementation.
- `docs/issues/incoming/` is still the local cross-project proposal area.
- Write all files as UTF-8 without a BOM and with LF line endings.
