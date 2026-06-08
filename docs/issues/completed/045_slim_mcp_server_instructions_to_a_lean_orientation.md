---
id: 45
status: completed
priority: medium
created: 2026-06-08
completed: 2026-06-08
stage: done
author: claude
title: Slim MCP server instructions to a lean orientation
---

# Issue #45: Slim MCP server instructions to a lean orientation

## Problem

The MCP server exposes its handoff protocol as `instructions=render_protocol(None)`
in `issuekit/mcp/server.py` (`create_server`). `render_protocol(None)`
concatenates the cycle overview plus all three role protocols (author,
implementer, reviewer). The measured size is about 2046 tokens, and it is loaded
into every MCP client session at connect time, even though a given session
usually acts as only one role. The two unused role protocols (roughly 1.2k to
1.4k tokens) are dead weight on every session.

This is the single largest controllable token cost in the agent handoff. Issue
bodies returned by tools are intrinsic spec content and are explicitly out of
scope here.

## Proposed Solution

Make the MCP server instructions a lean orientation instead of inlining every
role protocol, while keeping the on-demand `get_protocol` tool and the
`issuekit protocol` CLI fully self-contained (unchanged behavior).

1. Add a lean server-instructions renderer in `issuekit/protocol.py` (for
   example `render_server_instructions()`), returning the `CYCLE_PROTOCOL`
   overview plus a short pointer telling the agent to call
   `get_protocol(role="author"|"implementer"|"reviewer")` for the steps of its
   role. Do not inline the full role protocol text.
2. Change `create_server` in `issuekit/mcp/server.py` to use that lean renderer
   for the FastMCP `instructions` argument.
3. Do not change the `get_protocol` tool or the `issuekit protocol` CLI:
   `render_protocol(agent, role)` stays self-contained (cycle + role) so
   explicit fetches are unaffected. Keeping `get_protocol` self-contained is an
   intentional scope limit, not an oversight.

## Impact

- `issuekit/protocol.py`: new lean instructions renderer; existing
  `render_protocol` behavior for role/agent fetches is unchanged.
- `issuekit/mcp/server.py`: `create_server` uses the lean renderer for
  `instructions`.
- `tests/test_protocol.py`: lean instructions contain the cycle and a
  `get_protocol` pointer, name each role, and are substantially smaller than
  `render_protocol(None)`; role/agent renders remain self-contained.

## Implementation Plan

1. Add the lean renderer in `issuekit/protocol.py`, reusing `CYCLE_PROTOCOL`
   plus an ASCII pointer line that names the three roles and the
   `get_protocol(role=...)` call. Do not duplicate full role text.
2. Wire it into `create_server`'s `instructions` argument in
   `issuekit/mcp/server.py`.
3. Add tests asserting: the lean text includes the cycle heading and a
   `get_protocol(role=...)` pointer mentioning author, implementer, and
   reviewer; `len(lean)` is well under `len(render_protocol(None))`; and
   `render_protocol(role="reviewer")` (and the other roles) still include their
   role body.
4. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_protocol.py`
- Manual: call the lean renderer (or start the MCP server) and confirm the
  instructions are the lean orientation, not the full multi-role text.
- `uv run issuekit validate`

## Related Resources

- Issue #44 (introduced `CYCLE_PROTOCOL` and the all-roles render now used as
  server instructions)
- `issuekit/protocol.py` (`render_protocol`, `CYCLE_PROTOCOL`)
- `issuekit/mcp/server.py` (`create_server`, `instructions=render_protocol(None)`)
- Investigation finding: server instructions are about 2046 tokens per session;
  the lean target is a few hundred tokens.

## Handoff

- Summary: Implemented by kimi via issuekit implement.

**Completed**: 2026-06-08

## Completion Notes

- Approved by codex.
- Verification: `Reviewed by claude (distinct from implementer kimi; open review pool). kimi added render_server_instructions() returning CYCLE_PROTOCOL plus a lean SERVER_INSTRUCTIONS pointer that names author/implementer/reviewer and the get_protocol(role=...) calls; create_server now uses it for the FastMCP instructions argument. get_protocol and the issuekit protocol CLI are unchanged (render_protocol role/agent renders stay self-contained), matching the issue's intentional scope limit. Measured: server instructions drop from ~2046 to ~417 tokens (80% / ~1628 tokens saved per session); no role text is duplicated. Tests cover lean-includes-cycle+pointer+all-three-roles+ASCII, lean < full/2, and role renders remaining self-contained. Verified: uv run pytest (242 passed, 21 skipped), uv run issuekit validate (45 files, 0 warnings), uv run issuekit check-encoding clean. First kimi implement run via the delegation flow completed the headless implement -> submit_for_review handoff correctly in ~87s.`
