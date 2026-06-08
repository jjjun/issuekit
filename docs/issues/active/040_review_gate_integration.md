---
id: 40
status: in_progress
priority: low
created: 2026-06-08
completed: 
stage: review
implementer: codex
title: Route synchronous agent runs into the issuekit review gate
---

# Issue #40: Route synchronous agent runs into the issuekit review gate

## Problem

In headless mode there is no approval gate (see #37): the agent auto-executes
and the diff review is the only safety net. Today that review is informal. The
project already has a review workflow (issue #35 gates completion behind review;
issue #36 blocks implementer self-review), so synchronous runs should plug into
that gate rather than relying on an ad-hoc human glance.

## Proposed Solution

After `issuekit implement` (#39) produces changes, route the issue through the
existing review flow so completion stays gated behind a distinct reviewer.

1. On a successful implement run, advance the issue to stage `review` via the
   existing workflow (the implementing agent omits `reviewer` so the open pool
   handles it; same-name review still routes through the pool per #36).
2. Keep the "diff review is the only safety net" requirement enforced by
   process: completion requires reviewer approval (#35), and the approver must
   differ from the implementing session (#36).

## Impact

- `issuekit/commands/implement.py`: optionally submit for review after a run.
- `issuekit/workflow.py`: reuse `submit_for_review`/open-pool behavior; no new
  bypass of the existing self-review guards.
- `tests/`: an implement run advances the issue to `review` and respects the
  self-review block.

## Implementation Plan

1. After a successful implement run, call the existing `submit_for_review` path
   with `reviewer` omitted (open pool).
2. Ensure no new path lets the implementer self-approve; rely on #35 and #36.
3. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_implement_review_gate.py`
- Manual: implement an issue, confirm it lands in stage `review` in the open
  pool and cannot be self-approved by the implementing session.
- `uv run issuekit validate`

## Related Resources

- Issue #37 (no headless approval gate; diff review is the safety net)
- Issue #39 (the implement command this hooks into)
- Issue #35 (completion gated behind review) and Issue #36 (no self-review)
- `issuekit/workflow.py` (`submit_for_review`, open-pool reviewer resolution)

## Handoff

- Summary: Implement.py now claims the issue, runs the agent, and on a successful run submits for review with reviewer omitted (open pool). Failed/timed-out runs are not submitted. Added claim_issue() to workflow.py and tests covering review handoff, failed-run no-submit, and the self-review guard.
- Branch: `main`
- Commit: `c4ab26b`
