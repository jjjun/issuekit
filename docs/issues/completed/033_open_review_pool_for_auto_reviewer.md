---
id: 33
status: completed
priority: medium
created: 2026-06-08
completed: 2026-06-08
stage: done
title: Open review pool when default_reviewer is auto and reviewer is omitted
---

# Issue #33: Open review pool when default_reviewer is auto and reviewer is omitted

## Problem

With `default_reviewer = "auto"`, calling `submit_for_review` without an explicit
`reviewer` resolves the reviewer to `issue.assignee`. At submit time that is the
implementer, so the review is pinned to the implementer (for example `kimi`
reviewing its own work). See `_resolve_auto_reviewer` in `issuekit/workflow.py`:
when `require_distinct_reviewer` is false it returns `issue.assignee`.

The decision side then locks to that pinned assignee. `ensure_assigned_reviewer`
(`issuekit/workflow.py`) raises when a different agent tries to act, so a second
agent that passes `reviewer="claude"` to `approve` or `request_changes` is
rejected even though no one intentionally assigned the review.

The discovery side is already open: with `default_reviewer = "auto"`,
`next_review(reviewer=None)` lists every `stage=review` issue regardless of
assignee (`issuekit/mcp/server.py`). Only `submit_for_review` pinning and the
`ensure_assigned_reviewer` lock prevent an open, any-agent review pool.

Desired behavior: when `default_reviewer = "auto"` and `reviewer` is omitted at
submit, the review is an open pool that any configured agent can claim and
decide, credited to whichever agent performs the review. When `reviewer` is
passed explicitly, the review stays assigned and locked to that reviewer (current
behavior).

## Proposed Solution

Treat an empty review assignee as an "open" review that any configured agent may
decide, and have auto + omitted reviewer produce that open state at submit.

- `submit_for_review`: when `reviewer is None` and `config.default_reviewer ==
  "auto"`, set the review assignee to empty (`""`, open) instead of resolving to
  the implementer. When `reviewer` is provided, keep current behavior (assign and
  lock to that reviewer). Empty string is already a valid workflow token
  (`is_valid_workflow_token` in `issuekit/core.py`).
- `ensure_assigned_reviewer`: when `issue.assignee` is empty (open review), allow
  any configured reviewer instead of raising. Keep the existing lock when the
  review assignee is a concrete agent.
- `approve` and `request_changes`: on an open review, accept the caller-supplied
  `reviewer` and record the decision under that name (for example `approve`'s
  `Approved by {reviewer}` summary). Agents are expected to pass their own name;
  document the fallback if `reviewer` is omitted on an open review.
- Self-review safety with `require_distinct_reviewer = true`: because the
  reviewer is unknown at submit for an open review, enforce the
  "implementer cannot review own issue" rule at decision time (`approve` /
  `request_changes`) using `issue.implementer` versus the resolved reviewer,
  instead of only at submit.

## Impact

- `issuekit/workflow.py` (`submit_for_review`, `_resolve_auto_reviewer` usage,
  `ensure_assigned_reviewer`, `request_changes`, and self-review enforcement
  timing)
- `issuekit/mcp/server.py` (`approve` resolver, `request_changes`) and the CLI
  `submit-review` / `request-changes` paths as needed
- Behavior: auto + omitted reviewer yields an open review pool; explicit reviewer
  still locks; concrete `default_reviewer` is unchanged
- `tests/` (workflow and MCP server coverage)

## Implementation Plan

1. In `submit_for_review`, branch on `reviewer is None and
   config.default_reviewer == "auto"` to write an empty (open) review assignee;
   otherwise resolve and assign as today.
2. In `ensure_assigned_reviewer`, return early (allow any reviewer) when the
   issue's review assignee is empty.
3. Ensure `approve` and `request_changes` record the caller-supplied reviewer on
   an open review and do not raise for a non-matching name when the review is
   open.
4. Move the `require_distinct_reviewer` self-review check to decision time for
   open reviews so an implementer still cannot approve its own issue.
5. Add tests.

## Test Plan

- Submit with `reviewer` omitted and `default_reviewer="auto"` leaves the review
  assignee empty and `stage=review`.
- Submit with an explicit `reviewer` still assigns and locks to that reviewer.
- An open review can be approved by any configured agent (for example `claude`,
  `codex`, or `kimi`), recorded under the agent that approved it.
- An open review can be returned via `request_changes` by any configured agent.
- `next_review(reviewer=None)` under auto still lists the open review.
- With `require_distinct_reviewer = true`, the implementer cannot approve its own
  open review, but another agent can.
- A concrete `default_reviewer` (for example `"claude"`) is unaffected: review is
  assigned and locked as before.
- `uv run pytest`
- `uv run issuekit validate`
- `uv run issuekit check-encoding`

## Related Resources

- `issuekit/workflow.py` `_resolve_auto_reviewer`, `ensure_assigned_reviewer`,
  `ensure_not_self_review`, `submit_for_review`, `request_changes`
- `issuekit/mcp/server.py` `next_review`, `approve`, `_resolve_reviewer_for_issue`
- `issuekit/core.py` `is_valid_workflow_token` (empty token is valid)
- Follow-up from issue #32 (first-class kimi agent support)

## Handoff

- Summary: Open review pool when default_reviewer is auto and reviewer is omitted: empty assignee on submit, allow any reviewer on open reviews, self-review guard moved to decision time.
- Branch: `main`
- Commit: `a045c5a`

**Completed**: 2026-06-08

## Completion Notes

- Approved by kimi.
- Verification: `Reviewed branch main commit a045c5a against issue #33 scope. Confirmed: submit_for_review sets an empty (open) review assignee when reviewer is omitted and default_reviewer is auto, and keeps assign-and-lock when reviewer is explicit; ensure_assigned_reviewer early-returns for an empty (open) assignee; approve and request_changes record the caller-supplied reviewer on open reviews; require_distinct_reviewer self-review guard moved to decision time (ensure_not_self_review added in approve and request_changes); resolve_reviewer skips validation for empty reviewer. Verified: pytest 187 passed 18 skipped (6 new open-review tests plus updated former keep-assignee tests); issuekit validate 33 files 0 warnings; issuekit check-encoding clean.`
