# CLAUDE.md

## Handoff protocol

This repo uses the issuekit multi-agent handoff. For the current steps, run
`issuekit protocol --agent <agent>` (e.g. `codex`, `claude`, or `kimi`) or
`issuekit protocol --role <role>` (e.g. `implementer` or `reviewer`), or read
the issuekit MCP server instructions / `get_protocol` tool.

Do not copy the steps here; issuekit is the source of truth. Launch your agent
from the repo root so the MCP server resolves the correct `docs/issues/`
directory.

## Project notes

- Claude writes proposals, codex-ready issues, and reviews.
- If the proposal-system MCP tools hang or error, use the equivalent CLI with
  `--json` (`issuekit propose/incoming/adopt`); they share one implementation.
- Codex implements issuekit tasks from `docs/issues/active/`.
- Tracker conventions live in `docs/issues/README.md`.
- Write all files as UTF-8 without a BOM and with LF line endings.
- `docs/issues/` content must be English ASCII only.
