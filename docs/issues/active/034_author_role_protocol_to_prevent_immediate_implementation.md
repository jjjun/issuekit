---
id: 34
status: active
priority: high
created: 2026-06-08
completed:
title: Add author role protocol to prevent immediate implementation
---

# Issue #34: Add author role protocol to prevent immediate implementation

## Problem

When claude is absent and codex or kimi are asked to write an issue plan, the
protocol they receive tells them to implement immediately instead of stopping
after authoring.

`render_protocol` falls back to the implementer protocol for any agent that is
not an explicit reviewer: `issuekit/protocol.py:122` resolves the role with
`_AGENT_ROLE.get(agent, "implementer")`. The implementer text
(`CODEX_PROTOCOL`, `issuekit/protocol.py:35-48`) instructs the agent to run the
flow end to end: `claim_next_task` then implement on the current branch. There
is no protocol describing the authoring or planning role that CLAUDE.md assigns
to claude ("Claude writes proposals, codex-ready issues, and reviews").

As a result, an agent asked only to plan an issue treats "write the issue" as
"implement it now", skipping the intended implementer handoff.

## Proposed Solution

Add a distinct `author` role with its own protocol text, so planning and
implementation are separate handoffs.

- Add an `AUTHOR_PROTOCOL` constant in `issuekit/protocol.py` that instructs the
  author to: run `issuekit info` for the next id, create the issue under
  `docs/issues/active/` with `status: active`, an unstarted stage (`todo` or
  empty), and no assignee; then STOP. The author must not call
  `claim_next_task` or implement the issue in the same session. An implementer
  claims it later via `claim_next_task`.
- Register `"author"` in `_ROLE_PROTOCOLS`.
- Keep `_AGENT_ROLE` mapping agents to a default role, but make the author flow
  reachable through `issuekit protocol --role author`.
- Add `"author"` to the `--role` choices in `issuekit/cli.py` (protocol
  subparser, around `issuekit/cli.py:163`).

## Impact

- `issuekit/protocol.py`: new `AUTHOR_PROTOCOL`, `_ROLE_PROTOCOLS` entry, and
  `render_protocol` handling.
- `issuekit/cli.py`: `--role` choices for the protocol subparser.
- `tests/test_protocol.py`: cover author role rendering and the CLI choice.

## Implementation Plan

1. Add the `AUTHOR_PROTOCOL` constant and register `role="author"` in
   `_ROLE_PROTOCOLS`.
2. Confirm `render_protocol(role="author")` returns it and an unknown role
   still raises `ValueError`.
3. Add `"author"` to the `--role` choices in `issuekit/cli.py`.
4. Update `tests/test_protocol.py` for the new role and CLI option.

## Test Plan

- `uv run pytest tests/test_protocol.py`
- `uv run issuekit protocol --role author` prints the author protocol
- `uv run issuekit check-encoding`

## Related Resources

- `issuekit/protocol.py`
- `issuekit/cli.py`
- `CLAUDE.md` (Claude writes proposals, codex-ready issues, and reviews)
- Issue #35 (the completion-stage gate; sibling fix for handoff discipline)
