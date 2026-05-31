---
id: 12
status: completed
priority: medium
created: 2026-06-01
completed: 2026-06-01
stage: done
title: Add issuekit MCP server exposing handoff workflow tools
---



# Issue #12: Add issuekit MCP server exposing handoff workflow tools

## Problem

The workflow transitions from issue #11 are reachable only from the terminal.
For the two-agent loop we want codex and claude to call them as structured MCP
tools: codex asks for the next task and submits for review; claude asks for the
next review and approves or requests changes. Today neither agent can do this
as a typed function call against issuekit.

## Proposed Solution

Add an optional MCP server, `issuekit/mcp/server.py`, that exposes the issue #11
workflow functions as MCP tools over stdio (same pattern as the py_cr_wrapper
investigation MCP server). The server is a thin adapter: it imports
`issuekit.workflow` and `issuekit.core` and does no business logic of its own.
MCP is an optional dependency group so the core CLI stays dependency-free. A
single server with role-aware tools serves both agents; roles are expressed by
the `assignee` argument and issue `stage`, not by separate servers.

## Impact

- New: `issuekit/mcp/__init__.py`, `issuekit/mcp/server.py`
- Modified: `pyproject.toml` (add `mcp` dependency group + `issuekit-mcp`
  script entry)
- New: `tests/test_mcp_server.py`
- Modified: `README.md` (mention the MCP server and how to start it)

## Implementation Plan

1. Add to `pyproject.toml`:
   ```toml
   [dependency-groups]
   mcp = ["mcp>=1.0"]
   [project.scripts]
   issuekit-mcp = "issuekit.mcp.server:main"
   ```
   Keep the default install dependency-free; `mcp` is opt-in via
   `--group mcp`.
2. Implement `issuekit/mcp/server.py` with `FastMCP("issuekit")` and tools that
   wrap issue #11 functions. The working tree root is resolved the same way the
   CLI does (`Path.cwd()` + `load_config`), so the server operates on the
   consuming repo's `docs/issues`:
   - codex-facing: `claim_next_task(assignee="codex", priority=None)`,
     `submit_for_review(id, summary, branch=None, commit=None)`
   - claude-facing: `next_review()`, `request_changes(id, notes)`,
     `approve(id, verification)` (calls the `complete_issue` function extracted
     in issue #11; moves the issue to completed with `stage=done`; no completion
     logic is duplicated here)
   - shared: `get_issue(id)`, `list_queue(assignee=None, stage=None)`
   Each tool returns a compact dict (id, title, assignee, stage, file path,
   and the issue body for claim/get so the agent has the spec inline).
3. `main()` runs `mcp.run_stdio_async()` under `asyncio.run`, mirroring
   `py_cr_wrapper/mcp_server/server.py`.
4. Tool descriptions must state the protocol order: codex uses
   `claim_next_task` then `submit_for_review`; claude uses `next_review` then
   `approve`/`request_changes`.
5. All file mutations continue through the issue #11 functions (and thus
   `write_issue_atomic`); the MCP layer must not write frontmatter directly.

## Test Plan

- `uv run --group mcp pytest tests/test_mcp_server.py`
- Server registers the expected tool names.
- `claim_next_task` against a temp issues dir returns and claims the issue;
  a second call does not return the same issue.
- `submit_for_review` then `next_review` round-trips an issue from codex to
  claude; `approve` moves it to completed with `stage=done` (reuse complete
  logic); `request_changes` returns it to codex.
- Confirm `import issuekit.cli` still works without the `mcp` package installed
  (core stays dependency-free).

## Related Resources

- Issue #10, Issue #11 (required; the MCP server wraps #11)
- Reference implementation: `py_cr_wrapper/mcp_server/server.py`,
  `py_cr_wrapper/pyproject.toml` (`mcp` group + script entry)
- Issue #13 (register this server in Claude Code and codex)

## Notes

- Transport: stdio is sufficient when codex and claude run at different times
  (the issue files are the shared state and #11 claims are atomic). If they must
  run concurrently against one long-lived process, a future follow-up can add an
  HTTP/SSE transport; do not block this issue on it.

**Completed**: 2026-06-01

## Completion Notes

- Added optional FastMCP server and MCP test coverage for workflow tools.
- Verification: `uv run --group mcp pytest`
