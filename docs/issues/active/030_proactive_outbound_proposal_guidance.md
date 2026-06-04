---
id: 30
status: in_progress
priority: medium
created: 2026-06-04
completed: 
assignee: codex
stage: implementing
implementer: codex
title: Add proactive outbound proposal guidance to the handoff protocol
---

# Issue #30: Add proactive outbound proposal guidance to the handoff protocol

## Problem

The handoff protocol text in `issuekit/protocol.py` describes cross-project
proposals only reactively:

- the codex protocol says to inspect `issuekit incoming` "when cross-repo
  exchange is relevant", adopt after triage, and `propose --reply <id>` when
  completing an adopted issue that has an `origin:` field;
- the claude protocol only states that "Claude owns proposals and codex-ready
  issues".

There is no guidance for the proactive, outbound case: an agent working in a
consuming repo identifies a change that actually belongs to a *sibling* repo
(for example, a frontend session realizes the API server must change a response
shape). Because `create_server` passes `render_protocol(None)` as the MCP
`instructions`, every consuming repo already receives the proposal vocabulary in
context, yet the protocol never tells the agent that this situation is the
moment to originate a proposal. The likely default behavior is therefore to work
around the problem locally or to only report it to the user, never sending a
proposal.

A second gap reinforces this: even when an agent decides to propose, the
protocol does not point it at `issuekit list-refs` to discover the target repo's
ref name (the value needed for `propose --to <ref>`), so the agent does not know
where the proposal can be sent.

Net effect: the proposal system is well covered for receiving, triaging, and
replying, but the "I found a change that belongs to another repo" origination
path is not scaffolded, so it is rarely used unless a human explicitly asks.

## Proposed Solution

Add a short, ASCII "originating a proposal" paragraph to the protocol text so it
propagates to every repo automatically through `render_protocol` (MCP
`instructions` and the `get_protocol` tool / `issuekit protocol` CLI). No code
behavior changes; this is protocol-text only.

The new guidance should:

- tell the agent that when a needed change belongs to another registered repo,
  it should originate a proposal rather than only working around it locally or
  only reporting it to the user;
- point to `issuekit list-refs` to find the target repo's ref, then
  `issuekit propose --to <ref> --title <t> --body <b>` (or the MCP `propose`
  tool) to send it;
- note that proposing is non-destructive: it writes a suggestion into the target
  repo's `incoming/`, and the target repo owns triage (adopt or discard); the
  sender must not mutate the target repo's state directly;
- stay consistent with existing ownership wording (Claude owns proposals and
  codex-ready issues; in a consuming repo, whichever agent role spots the
  cross-repo need may originate the proposal).

Placement: the guidance must appear in the output of `render_protocol(None)` (the
MCP instructions) and in both single-agent renders. Today the cross-project
context paragraph lives only in `CODEX_PROTOCOL`, so `get_protocol("claude")`
does not carry it. Add the origination guidance to both `CODEX_PROTOCOL` and
`CLAUDE_PROTOCOL` (a concise sentence or two in each) so either agent role gets
it, regardless of how the protocol is fetched. Keep it brief; do not duplicate
the full reactive paragraph.

## Impact

- Modified: `issuekit/protocol.py` (`CODEX_PROTOCOL` and `CLAUDE_PROTOCOL` text).
- Modified: `tests/test_protocol.py` (assert the new guidance is present in both
  agents and in the combined render, and that output stays ASCII).
- Optional: `docs/issues/README.md` ("Cross-Project Proposals") gains one human
  facing sentence about originating proposals via `list-refs` + `propose`.
- No change to commands, tools, JSON shapes, exit codes, or the issue lifecycle.

## Implementation Plan

1. In `issuekit/protocol.py`, add a concise "originating a proposal" paragraph to
   `CODEX_PROTOCOL` (near the existing cross-project paragraph) and an equivalent
   concise paragraph to `CLAUDE_PROTOCOL`. Cover: recognize the change belongs to
   a sibling repo, use `issuekit list-refs` to find the ref, send via
   `issuekit propose --to <ref> --title <t> --body <b>` (or MCP `propose`),
   proposing is non-destructive and the target repo triages. ASCII only.
2. Keep wording short and aligned with the existing tone; do not restate the full
   reactive flow. Ensure both single-agent renders and `render_protocol(None)`
   include the new text.
3. Update `tests/test_protocol.py` to assert a stable phrase from the new
   guidance (for example "list-refs" and an "originate"/"belongs to another repo"
   marker) appears in `render_protocol("codex")`, `render_protocol("claude")`,
   and `render_protocol(None)`, and keep the existing `both.encode("ascii")`
   ASCII guarantee.
4. Optionally add one sentence to `docs/issues/README.md` under "Cross-Project
   Proposals" describing the outbound origination path for human readers.

## Test Plan

- `uv run pytest tests/test_protocol.py` passes, including new assertions that the
  origination guidance is present for codex, claude, and both, and that the
  rendered text remains ASCII.
- `uv run pytest` (full suite) stays green.
- `uv run issuekit validate` and `uv run issuekit check-encoding` pass.
- Manual: `issuekit protocol --agent claude` and `issuekit protocol --agent codex`
  both show the new origination guidance.

## Related Resources

- `issuekit/protocol.py` (`CODEX_PROTOCOL` L6, `CLAUDE_PROTOCOL` L60,
  `render_protocol` L94)
- `issuekit/mcp/server.py` (`FastMCP("issuekit", instructions=render_protocol(None))`
  L36; `propose` / `list_incoming` / `adopt_proposal` tools)
- `issuekit/commands/propose.py` (`run_list_refs`, `build_proposal`)
- `tests/test_protocol.py`
- `docs/issues/README.md` ("Cross-Project Proposals")
