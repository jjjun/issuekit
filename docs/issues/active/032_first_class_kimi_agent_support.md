---
id: 32
status: in_progress
priority: medium
created: 2026-06-08
completed: 
assignee: kimi
stage: implementing
implementer: kimi
title: First-class kimi agent support in issuekit
---

# Issue #32: First-class kimi agent support in issuekit

## Problem

A new agent, `kimi`, now participates in the handoff alongside `codex` and
`claude`. The toolkit recognizes an agent only when it appears in
`config.assignees` (`_validate_assignee` in `issuekit/workflow.py` rejects any
other name), and three places are still hardcoded to the two original agents:

- `IssuekitConfig.assignees` defaults to `("codex", "claude")` in
  `issuekit/config.py`, so any repo without an explicit override does not know
  about `kimi`.
- `issuekit/protocol.py` exposes protocol text only for `codex` and `claude`.
  `render_protocol("kimi")` raises `ValueError: unknown agent: kimi`, which also
  breaks the MCP `get_protocol(agent="kimi")` tool.
- `issuekit/cli.py` restricts `protocol --agent` to `choices=("codex",
  "claude")`, so `issuekit protocol --agent kimi` is rejected by argparse.

This repo's `pyproject.toml` was already given an `assignees` override that
includes `kimi` as a bootstrap so the work below can be claimed. The remaining
gaps are in the issuekit package itself.

## Proposed Solution

Make agent identity data-driven rather than hardcoded to two names, and treat
the published protocols as role-based (implementer flow vs reviewer flow) that
any configured agent can read.

- Add `kimi` to the default `IssuekitConfig.assignees` tuple in
  `issuekit/config.py` so fresh repos recognize the agent without an override.
- Update `issuekit/protocol.py` so a protocol can be rendered for `kimi`
  without raising. Preferred approach: keep the two role protocols
  (implementer/reviewer) and map agents to a role, so `render_protocol("kimi")`
  returns the implementer flow (kimi is a worker) while still allowing the
  reviewer flow when kimi reviews. A minimally acceptable approach is adding a
  `kimi` entry to `PROTOCOLS` that points at the implementer protocol. The
  protocol bodies already state that any configured agent can be implementer or
  reviewer, so no agent-specific prose is required.
- Update the `protocol --agent` argument in `issuekit/cli.py` so `kimi` is a
  valid value (extend the choices, or drop the static `choices` and validate
  against the known agents/roles).
- Keep the MCP `get_protocol` tool working for `kimi` (it should follow from the
  `render_protocol` change; no separate hardcoding).
- Update the handoff reference docs (`CLAUDE.md`, `AGENTS.md`, and the
  `handoff_reference.md` template under `issuekit/templates/`) so the documented
  `issuekit protocol --agent ...` guidance mentions `kimi` / role usage instead
  of implying only two agents.

## Impact

- `issuekit/config.py` (default assignees)
- `issuekit/protocol.py` (agent/role rendering)
- `issuekit/cli.py` (`protocol --agent` choices)
- `issuekit/templates/handoff_reference.md`, `CLAUDE.md`, `AGENTS.md` (docs)
- `tests/` (protocol, cli, config coverage)
- Behavior: `kimi` becomes a recognized assignee/reviewer and can read a
  protocol through both the CLI and the MCP `get_protocol` tool.

## Implementation Plan

1. Add `kimi` to the default `assignees` tuple in `issuekit/config.py`.
2. Generalize `issuekit/protocol.py` so `render_protocol("kimi")` returns a
   protocol (implementer flow) instead of raising; keep `codex` and `claude`
   behavior unchanged.
3. Update `protocol --agent` in `issuekit/cli.py` to accept `kimi`.
4. Update handoff docs and the `handoff_reference.md` template to describe
   per-agent / role protocol selection including `kimi`.
5. Add tests covering the new config default, `render_protocol("kimi")`, and
   `issuekit protocol --agent kimi`.

## Test Plan

- `render_protocol("kimi")` returns non-empty protocol text and does not raise.
- `issuekit protocol --agent kimi` exits 0 and prints a protocol.
- A repo with no `[tool.issuekit]` override reports `kimi` as a valid assignee
  (claiming/submitting/reviewing as `kimi` is not rejected by
  `_validate_assignee`).
- `uv run pytest`
- `uv run issuekit validate`
- `uv run issuekit check-encoding`

## Related Resources

- `issuekit/workflow.py` `_validate_assignee` (assignee gate)
- `issuekit/config.py` `IssuekitConfig.assignees`
- `issuekit/protocol.py` `PROTOCOLS` / `render_protocol`
- `issuekit/cli.py` `protocol` subparser
- Bootstrap: `pyproject.toml` `[tool.issuekit] assignees` now includes `kimi`
