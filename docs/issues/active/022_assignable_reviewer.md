---
id: 22
status: active
priority: medium
created: 2026-06-01
completed:
title: Make the reviewer assignable (codex or claude) via MCP and config
---


# Issue #22: Make the reviewer assignable (codex or claude) via MCP and config

## Problem

Review is hardcoded to claude. `workflow.submit_for_review` /
`request_changes` already accept a `reviewer` argument, but the MCP server does
not expose it: `next_review()` queries `find_for(..., "claude", ...)`, `approve`
writes a fixed "Approved by claude." note, and the tool descriptions and
`protocol.py` all state claude is the sole reviewer. So in practice only claude
can review, and there is no way to route a review to codex (for example when
claude is unavailable). We want the reviewer to be selectable per repo and per
call, while still preventing self-review (issue #21).

## Proposed Solution

Expose `reviewer` through the MCP tools and add a `default_reviewer` config key,
so reviews can be routed to codex or claude. Build on issue #21's `implementer`
field and self-review guard so a selectable reviewer still cannot approve its own
work. Update the canonical protocol text to describe role assignment by
`assignee` rather than a fixed claude-reviews rule.

## Impact

- Modified: `issuekit/config.py` (`IssuekitConfig.default_reviewer`,
  default "claude")
- Modified: `issuekit/mcp/server.py` (`submit_for_review`, `next_review`,
  `request_changes`, `approve` take/use `reviewer`)
- Modified: `issuekit/workflow.py` (approve/complete-time self-review guard using
  the reviewer; reviewer defaults from config)
- Modified: `issuekit/commands/complete.py` (accept the reviewer for the
  completion note and the self-review check)
- Modified: `issuekit/protocol.py` (role-neutral wording)
- Modified: `issuekit/cli.py` (optional `--reviewer` on `submit-review` /
  `request-changes` for parity)
- New/Modified tests: `tests/test_mcp_server.py`, `tests/test_workflow.py`,
  `tests/test_config.py`, `tests/test_protocol.py`
- Modified: `README.md`

## Implementation Plan

1. Config: add `default_reviewer: str = "claude"` to `IssuekitConfig`, loaded
   from `[tool.issuekit]` / `issuekit.toml` (reuse the #20 source resolution).
   Validate it with the existing token-shape + allowed-`assignees` check.
2. MCP server (`issuekit/mcp/server.py`):
   - `submit_for_review(id, summary, branch=None, commit=None, reviewer=None)`:
     resolve `reviewer = reviewer or config.default_reviewer`, pass to
     `workflow.submit_for_review`. The #21 guard rejects reviewer == implementer.
   - `next_review(reviewer=None)`: resolve from config default; query
     `find_for(issues_dir, reviewer, stage="review")`. Return that reviewer in
     the empty payload.
   - `request_changes(id, notes, reviewer=None)`: resolve and pass through.
   - `approve(id, verification, reviewer=None)`: resolve reviewer; pass it to
     `complete_issue` so the completion note is "Approved by <reviewer>." and the
     self-review guard (reviewer != implementer) is enforced at approve time too.
   - Update tool descriptions to be role-neutral (no "Claude protocol step";
     say "Reviewer step", and note the reviewer defaults to default_reviewer).
3. workflow/complete: thread `reviewer` into the approve/complete path and apply
   `ensure_not_self_review` (from #21) at approve time, not only at submit time,
   so routing a review to the implementer is blocked even if defaults change.
4. protocol.py: rewrite so it describes the queue model: any configured agent in
   `assignees` can implement or review; the reviewer is whoever the issue's
   `assignee` is at `stage=review`, must not be the `implementer`, and defaults
   to `default_reviewer`. Keep it ASCII and concise; this text propagates to all
   repos via `get_protocol` / `issuekit protocol`.
5. CLI parity (small): add optional `--reviewer` to `submit-review` and
   `request-changes` so the CLI path matches the MCP path.
6. Keep backward compatibility: with no config and no argument, behavior is
   identical to today (claude reviews), so existing repos are unaffected until
   they set `default_reviewer` or pass `reviewer`.

## Test Plan

- `uv run pytest tests/test_mcp_server.py tests/test_workflow.py
  tests/test_config.py tests/test_protocol.py`
- Default unchanged: with no config/arg, `next_review` returns claude's queue and
  `approve` writes "Approved by claude." (existing tests still pass).
- Reviewer routing: `submit_for_review(reviewer="codex")` on a claude-implemented
  issue sets assignee=codex/stage=review; `next_review(reviewer="codex")` returns
  it; `approve(reviewer="codex", ...)` completes it with "Approved by codex."
- Config default: with `default_reviewer="codex"`, `next_review()` (no arg)
  returns codex's review queue.
- Self-review still blocked (from #21): `submit_for_review(reviewer="codex")` on a
  codex-implemented issue raises; `approve(reviewer=<implementer>)` raises.
- Protocol: `issuekit protocol` and MCP `get_protocol` return the role-neutral
  text and are identical (single source); output is ASCII.
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/mcp/server.py` (`next_review`, `submit_for_review`,
  `request_changes`, `approve`)
- `issuekit/workflow.py` (`submit_for_review`/`request_changes` already take
  `reviewer`)
- `issuekit/config.py` (#20 config source resolution), `issuekit/protocol.py`
- Issue #21 (implementer field + self-review guard; required)
