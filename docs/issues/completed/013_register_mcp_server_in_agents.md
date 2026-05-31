---
id: 13
status: completed
priority: medium
created: 2026-06-01
completed: 2026-06-01
stage: done
title: Register issuekit MCP server in Claude Code and codex
---



# Issue #13: Register issuekit MCP server in Claude Code and codex

## Problem

The issuekit MCP server from issue #12 exists but is not wired into either
agent, and the two-agent handoff protocol is not written down anywhere. Without
registration and a documented protocol, codex and claude will not discover the
tools, and there is no shared convention for which tools each agent calls in
which order.

## Proposed Solution

Register the `issuekit-mcp` server for both agents (Claude Code via `.mcp.json`,
codex via `.codex/config.toml`) and document the handoff protocol in the agent
instruction files so each agent knows its role and the exact tool sequence.

## Impact

- New: `.mcp.json` (issuekit repo root, for Claude Code auto-discovery)
- Modified: `.codex/config.toml` (add the MCP server block; create the file if
  absent)
- Modified: `AGENTS.md` (codex protocol), `CLAUDE.md` (claude protocol)
- Modified: `README.md` (one paragraph + link to the protocol)

## Implementation Plan

1. Add `.mcp.json` at the repo root:
   ```json
   {
     "mcpServers": {
       "issuekit": {
         "command": "uv",
         "args": ["run", "--group", "mcp", "issuekit-mcp"],
         "cwd": "${workspaceFolder}"
       }
     }
   }
   ```
   (Same shape as `py_cr_wrapper/.mcp.json`.)
2. In `.codex/config.toml`, add an MCP server entry:
   ```toml
   [mcp_servers.issuekit]
   command = "uv"
   args = ["run", "--group", "mcp", "issuekit-mcp"]
   ```
   Verify the exact key against the installed codex CLI
   (`codex --help` / current docs); adjust if the schema differs by version.
3. In `AGENTS.md` add a "Handoff protocol (codex)" section: codex calls
   `claim_next_task(assignee="codex")`, implements the issue, commits on a
   branch, then `submit_for_review(id, summary, branch, commit)`. If sent back
   (`stage=changes_requested`), re-claim and address the feedback.
4. In `CLAUDE.md` add a "Handoff protocol (claude)" section: claude calls
   `next_review()`, reviews the referenced branch/commit diff, then either
   `approve(id, verification)` or `request_changes(id, notes)`. Claude does not
   implement; it designs, files issues, and reviews (consistent with the
   existing role split).
5. Add a short README paragraph pointing to both protocol sections.

## Test Plan

- This issue is configuration + docs; no unit tests.
- Manual: start Claude Code in the repo and confirm the `issuekit` MCP server
  connects and its tools are listed.
- Manual: start codex in the repo and confirm it discovers the `issuekit` MCP
  server.
- End-to-end smoke: file a throwaway issue, have codex `claim_next_task` +
  `submit_for_review`, then claude `next_review` + `approve`; confirm the issue
  ends up in `completed/` and `issuekit validate` passes.

## Related Resources

- Issue #12 (required; provides the `issuekit-mcp` server and script entry)
- `py_cr_wrapper/.mcp.json`, `py_cr_wrapper/.codex/config.toml` (registration
  reference)
- `AGENTS.md`, `CLAUDE.md` (existing role guidance to extend)

**Completed**: 2026-06-01

## Completion Notes

- Registered the issuekit MCP server and documented codex/claude handoff protocol.
- Verification: `uv run issuekit validate; uv run issuekit check-encoding`
