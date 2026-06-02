---
id: 23
status: in_progress
priority: medium
created: 2026-06-01
completed: 
assignee: codex
stage: implementing
title: Make the self-review guard optional and add an auto reviewer default
---

# Issue #23: Make the self-review guard optional and add an auto reviewer default

## Problem

Issue #21 added a self-review guard keyed on agent NAME: `ensure_not_self_review`
raises when `reviewer == issue.implementer`. But in practice the same agent kind
running in a different context/session is a legitimate third-party reviewer
(codex context 1 implements, codex context 2 reviews). issuekit only sees the
agent name string, not the context, so the name-based guard wrongly blocks that
valid case. We want "either codex or claude may review" to be the default, while
still letting strict repos forbid same-name review.

Also, `default_reviewer` is a single fixed value, so there is no way to say
"review goes to whoever is not the implementer" automatically.

## Proposed Solution

1. Make the self-review guard opt-in via config (default OFF), so by default a
   review may be assigned to any configured agent, including the same name as the
   implementer (third-party-ness is ensured by running a separate context, which
   issuekit does not police).
2. Add `default_reviewer = "auto"`, meaning: pick the reviewer based on the
   guard. With the guard OFF, `auto` keeps the current assignee/explicit choice
   or falls back to a configured default; with the guard ON, `auto` resolves to
   an agent that is not the implementer.

This keeps full backward compatibility: a repo that sets the guard ON and a
concrete `default_reviewer` behaves exactly like today.

## Impact

- Modified: `issuekit/config.py` (new `require_distinct_reviewer: bool = False`;
  allow `default_reviewer = "auto"`)
- Modified: `issuekit/workflow.py` (`ensure_not_self_review` becomes config-gated;
  `resolve_reviewer` handles `auto` and the implementer)
- Modified: `issuekit/commands/complete.py` (approve path passes the issue +
  config so `auto`/guard resolve consistently)
- Modified: `issuekit/protocol.py` (describe auto + optional guard)
- Modified: `README.md`
- New/Modified tests: `tests/test_config.py`, `tests/test_workflow.py`,
  `tests/test_mcp_server.py`

## Implementation Plan

1. Config (`issuekit/config.py`):
   - Add `require_distinct_reviewer: bool = False` to `IssuekitConfig`, loaded
     from `[tool.issuekit]` / `issuekit.toml` (reuse #20 source resolution;
     coerce to bool).
   - Allow `default_reviewer = "auto"`. Update `_validate_default_reviewer` so
     `"auto"` is accepted in addition to a member of `assignees` (still reject
     other unknown tokens / bad shape).
2. Reviewer resolution (`issuekit/workflow.py`):
   - Change `resolve_reviewer` to take the issue's implementer (or the issue) and
     the config, so it can resolve `auto`:
     - If an explicit reviewer is passed, use it (subject to the guard below).
     - Else if `default_reviewer != "auto"`, use `default_reviewer`.
     - Else (`auto`): if `require_distinct_reviewer` is ON, choose the configured
       assignee that is not the implementer (for the two-agent case this is
       deterministic: the other agent); if OFF, fall back to a stable default
       (for example the first configured assignee, or keep the existing assignee)
       and document the choice.
   - Make `ensure_not_self_review` a no-op unless `require_distinct_reviewer` is
     True. Call sites stay the same (`submit_for_review`, `complete_issue`), but
     pass `config` so the guard can check the flag. When ON, behavior matches
     issue #21 exactly.
3. complete/approve (`issuekit/commands/complete.py`): already passes `config`
   and `reviewer`; update the `resolve_reviewer` call to the new signature and
   keep the approve-time guard gated by the same flag.
4. protocol.py: note that the reviewer defaults to `default_reviewer`, which may
   be `auto`; and that same-name review is allowed unless
   `require_distinct_reviewer` is set. Keep ASCII; this propagates via
   `get_protocol` / `issuekit protocol`.
5. Defaults: `require_distinct_reviewer=False`, `default_reviewer="claude"`
   stays the shipped default so existing repos are unchanged. A repo wanting
   "either may review" sets `default_reviewer = "auto"` (guard already off). A
   strict repo sets `require_distinct_reviewer = true`.

## Test Plan

- `uv run pytest tests/test_config.py tests/test_workflow.py
  tests/test_mcp_server.py`
- Config: `require_distinct_reviewer` loads as bool; `default_reviewer = "auto"`
  is accepted; an unknown non-auto reviewer still raises.
- Guard OFF (default): `submit_for_review(reviewer="codex")` on a
  codex-implemented issue SUCCEEDS (no self-review error); `approve` by the same
  name also succeeds.
- Guard ON: same call RAISES, matching issue #21 behavior (regression check).
- auto + guard OFF: `default_reviewer="auto"`, no explicit reviewer -> resolves
  to the documented stable default and review proceeds for either agent.
- auto + guard ON: `default_reviewer="auto"` on a codex-implemented issue
  resolves the reviewer to claude (the not-implementer); on a claude-implemented
  issue resolves to codex.
- Backward compat: with shipped defaults, all existing #21/#22 tests pass
  unchanged (claude reviews; explicit reviewer still works).
- Protocol text is ASCII and identical via CLI and MCP `get_protocol`.
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/workflow.py` (`ensure_not_self_review` L253, `resolve_reviewer`
  L260, call sites L146/L168)
- `issuekit/config.py` (`default_reviewer`, `_validate_default_reviewer` L73)
- `issuekit/commands/complete.py` (`complete_issue` L76/L84 guard + resolve)
- `issuekit/protocol.py`
- Issue #21 (the name-based guard this makes optional), Issue #22 (assignable
  reviewer + `default_reviewer`)
