---
id: 43
status: active
priority: high
created: 2026-06-08
completed:
stage: todo
title: Enforce author is not the implementer while allowing author to review
---

# Issue #43: Enforce author is not the implementer while allowing author to review

## Problem

#34 asks an author to STOP after writing an issue and not implement it in the
same session, but that is only protocol prose. Once #42 records `author` in
frontmatter, the discipline can be enforced in data, closing the
separation-of-duties triangle:

- implementer != reviewer is already enforced (#36).
- author != implementer is only requested, not enforced.
- author == reviewer should remain allowed (the spec owner verifying the result
  is desirable), so the implementer != reviewer guard must NOT be widened to
  block the author.

Without enforcement, an agent asked only to plan can still claim and implement
its own issue, defeating the fresh-eyes handoff #34 intends.

## Proposed Solution

Add a claim-time guard that blocks an agent from implementing an issue it
authored, reusing the open-pool same-name exception pattern from #36/#33.

1. In the claim path (`claim_issue` / `claim_next_task`), reject a claim when
   the claiming agent equals the recorded `author`, with a clear error.
2. Mirror #36: allow a same-name implementer only when the issue was routed
   through the open implement pool (unassigned), not when the author explicitly
   self-assigns. Reuse existing open-pool resolution rather than adding a new
   bypass.
3. Confirm the review guards (#36) block only implementer == reviewer, and that
   author == reviewer stays allowed end to end through `submit_for_review` /
   `next_review` / `approve`.

## Impact

- `issuekit/workflow.py`: author-aware claim guard; ensure review-side guards
  do not reject author == reviewer.
- `issuekit/commands/implement.py`: surface the guard error cleanly (it already
  routes `WorkflowError`).
- `tests/`: an author cannot claim its own issue; a different agent can; the
  open-pool same-name exception behaves like #36; author can review and approve.

## Implementation Plan

1. Read `author` in the claim path and reject self-implementation, with the
   open-pool same-name exception.
2. Audit `submit_for_review` / `approve` so author == reviewer is not blocked by
   the implementer != reviewer guard.
3. Add tests for both the block and the allowed author-review path.
4. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_author_separation.py`
- Manual: author issue with `--agent codex`, then `issuekit implement <id>
  --agent codex` is rejected; `--agent kimi` is accepted; the codex author can
  later review and approve the kimi implementation.
- `uv run issuekit validate`

## Related Resources

- Issue #42 (records the `author` field this guard consumes)
- Issue #36 (implementer != reviewer guard and open-pool same-name exception)
- Issue #33 (open review pool pattern reused for the open implement pool)
- Issue #34 (author handoff this enforces)
- `issuekit/workflow.py`, `issuekit/commands/implement.py`
