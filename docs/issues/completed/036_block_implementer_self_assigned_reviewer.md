---
id: 36
status: completed
priority: high
created: 2026-06-08
completed: 2026-06-08
stage: done
title: Block implementer from self-assigning as reviewer on submit
---

# Issue #36: Block implementer from self-assigning as reviewer on submit

## Problem

An implementer can route the review of its own work back to itself, producing a
self-review. This happened in practice while completing #35: the issue reached
stage `review` with both `assignee: kimi` and `implementer: kimi`, so the
implementer was also the assigned reviewer.

The root cause is that `submit_for_review` accepts an arbitrary `reviewer`
argument and only guards against self-review when `require_distinct_reviewer` is
true (`issuekit/workflow.py:146-150`, `ensure_not_self_review` at
`issuekit/workflow.py:272`). That guard compares by agent name, which is the
wrong lever for this project: agents are identified only by name token
(`codex`, `claude`, `kimi`), with no per-session identity. A legitimate
workflow here is "codex in one thread implements, codex in another thread
reviews" - both are the name `codex`, so a name-based distinct-reviewer rule
would wrongly forbid that valid hand-off.

The open review pool already provides "anyone may review": with
`default_reviewer = auto` and `reviewer` omitted, `submit_for_review` leaves
`assignee = ""` and any agent can pick the issue up via `next_review` and
`approve`. The real gap is only that the implementer can *explicitly name
itself* as the reviewer at submit time instead of leaving the issue in the open
pool.

## Proposed Solution

Stop the implementer from self-assigning as reviewer at submit time, without
breaking same-name (e.g. codex -> codex) review through the open pool.

1. In `submit_for_review`, reject a `reviewer` argument that equals the issue's
   implementer (the submitting `assignee`), regardless of
   `require_distinct_reviewer`. The error should tell the caller to omit
   `reviewer` so the issue goes to the open pool, where another session or agent
   reviews it.
2. Keep the existing open-pool behavior intact: omitting `reviewer` under
   `default_reviewer = auto` still leaves `assignee = ""`, so same-name review
   (codex thread A -> codex thread B) keeps working by routing through the pool
   rather than an explicit self-assignment.
3. Document the residual limit honestly: because actors are identified only by
   name, the system cannot mechanically stop the *same* codex session from
   approving its own open-pool issue. Cover this with protocol text that
   requires a different session/agent to approve than the one that implemented.
   Note that strict per-actor enforcement would need a future session-id
   concept and is out of scope here.

## Impact

- `issuekit/workflow.py`: reject implementer == reviewer in `submit_for_review`.
- `issuekit/protocol.py`: add guidance that the approving session/agent must
  differ from the implementing one, and that same-name review goes through the
  open pool (omit reviewer).
- `docs/issues/README.md`: document the submit-time self-assignment rule and the
  open-pool same-name review convention.
- `tests/`: cover the new rejection and confirm open-pool same-name review still
  succeeds.

## Implementation Plan

1. In `submit_for_review`, after resolving the reviewer, raise `WorkflowError`
   when the resolved reviewer is non-empty and equals `issue.implementer` (or
   the submitting `assignee`). Apply this independent of
   `require_distinct_reviewer`. Keep the omitted-reviewer open-pool path
   unchanged.
2. Add protocol text in `issuekit/protocol.py` (implementer and reviewer
   sections) stating: implementers omit `reviewer` to use the open pool; the
   approving session/agent must not be the same session that implemented.
3. Update `docs/issues/README.md` accordingly.
4. Add tests: submit with `reviewer == implementer` is rejected; submit with
   reviewer omitted (auto) still yields an open-pool issue that another agent
   can `approve`; same-name (codex -> codex) review through the pool works.
5. Run `uv run pytest`, `uv run issuekit validate`, and
   `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_workflow.py tests/test_mcp_server.py tests/test_protocol.py`
- Manual: claim as codex, then `submit-review --reviewer codex` is rejected;
  `submit-review` with reviewer omitted leaves the issue open-pool; a second
  actor can `approve` it.
- `uv run issuekit validate`
- `uv run issuekit check-encoding`

## Related Resources

- `issuekit/workflow.py` (`submit_for_review`, `ensure_not_self_review`,
  `resolve_reviewer`, `_resolve_auto_reviewer`)
- `issuekit/protocol.py`
- `docs/issues/README.md`
- Issue #35 (where this self-review assignment surfaced)
- Issues #21 and #23 (self-review guard and auto-reviewer history)
- Note: actors are identified only by name, so same-name self-approval in the
  open pool cannot be blocked mechanically without a session-id concept; this
  issue handles the submit-time self-assignment and documents the residual gap.

## Handoff

- Summary: Block implementer from self-assigning as reviewer on submit. Reject explicit reviewer == implementer in submit_for_review regardless of require_distinct_reviewer. Keep open-pool same-name review intact by omitting reviewer. Update protocol text and docs/issues/README.md. Add tests.
- Branch: `main`
- Commit: `3d8c0cb`

**Completed**: 2026-06-08

## Completion Notes

- Approved by claude.
- Verification: `uv run pytest (195 passed, 18 skipped); uv run issuekit validate (40 files, 0 warnings); uv run issuekit check-encoding (clean). Reviewed by claude; implementer was codex (distinct reviewer).`
