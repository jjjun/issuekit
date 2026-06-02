---
id: 21
status: active
priority: medium
created: 2026-06-01
completed:
title: Record the implementer and block self-review in workflow transitions
---


# Issue #21: Record the implementer and block self-review in workflow transitions

## Problem

We want reviews to be assignable to either agent (codex or claude), not only
claude (see issue #22). But once any agent can review, an agent could review and
approve its own implementation, which removes the third-party check that gives
review its value. The current model cannot prevent this: an issue carries only
`assignee` (a queue pointer) and `stage`. When `claim_next` is called the
`assignee` is set to the implementer, but `submit_for_review` overwrites
`assignee` with the reviewer, so the identity of the implementer is lost. There
is no field that says who implemented the issue, so "implementer must not be the
reviewer" cannot be enforced structurally.

## Proposed Solution

Add an `implementer` frontmatter field that records who claimed/implemented the
issue, set on `claim_next` and preserved across `submit_for_review` /
`request_changes`. Then enforce, in the approve/complete path and in
`submit_for_review`, that the reviewer is not the implementer. This is the
structural foundation that issue #22 (assignable reviewer) builds on. This issue
does not change who can review yet; it only records the implementer and adds the
guard, with claude still the default reviewer.

## Impact

- Modified: `issuekit/core.py` (Issue dataclass + parsing + frontmatter format:
  add `implementer`)
- Modified: `issuekit/workflow.py` (`claim_next` sets `implementer`;
  `submit_for_review` / `request_changes` preserve it; add self-review guard)
- Modified: `issuekit/commands/complete.py` (`complete_issue` keeps/owns the
  guard path; clears `implementer` like `assignee` on completion)
- New/Modified tests: `tests/test_workflow_model.py`, `tests/test_workflow.py`
- Modified: `docs/issues/README.md` (document the optional `implementer` field)

## Implementation Plan

1. In `issuekit/core.py`, add `implementer: str` to the `Issue` dataclass, parse
   it in `read_issues` via `_normalize(metadata.get("implementer"))`, and emit it
   in `format_issue_frontmatter` only when non-empty (same pattern as
   `assignee`/`stage`, so existing files stay byte-identical). Keep field order:
   id, status, priority, created, completed, assignee, stage, implementer, title.
   Reuse the existing token-shape validation for its value.
2. In `issuekit/workflow.py`:
   - `claim_next`: when claiming, set `implementer=<assignee>` (the agent taking
     the work) in addition to `assignee`/`stage`. On a re-claim of a
     `changes_requested` issue, set `implementer` to the re-claiming agent.
   - `submit_for_review` and `request_changes`: pass `implementer` through
     unchanged (do not drop it when rewriting frontmatter).
   - Add a guard helper `ensure_not_self_review(issue, reviewer)` that raises
     `WorkflowError` if `reviewer == issue.implementer`. Call it in
     `submit_for_review` (reject submitting to a reviewer who is the implementer)
     so a self-review cannot even be set up. Keep claude as the default reviewer
     for now.
3. In `issuekit/commands/complete.py` `complete_issue`: clear `implementer`
   (set to "") on completion, mirroring how `assignee` is cleared and
   `stage=done` is set. (Approve-time reviewer-identity enforcement lands in #22
   when approve takes a reviewer argument; here just keep the field consistent.)
4. `_write_active_issue` already rewrites the full frontmatter dict; include
   `implementer` so transitions never silently drop it.
5. Update `docs/issues/README.md` to mention `implementer` as an
   optional, tool-managed field (agents do not hand-edit it).

## Test Plan

- `uv run pytest tests/test_workflow_model.py tests/test_workflow.py`
- Parsing/format: an issue with `implementer: codex` round-trips; an issue
  without it serializes byte-identically (no spurious line).
- `claim_next("codex")` sets `implementer=codex`; after `submit_for_review` the
  `implementer` is still `codex` while `assignee` becomes the reviewer.
- `request_changes` back to codex, then re-`claim_next("codex")`: `implementer`
  stays/refreshes to codex and is preserved through the next submit.
- Self-review guard: `submit_for_review(reviewer="codex")` on an issue whose
  `implementer` is `codex` raises `WorkflowError`; `reviewer="claude"` succeeds.
- `complete_issue` clears `implementer` (completed file has no `implementer`
  line) and still sets `stage=done`, clears `assignee`.
- Byte-level: no BOM/CRLF in rewritten files. Run full `uv run pytest` and
  `uv run issuekit validate`.

## Related Resources

- `issuekit/core.py` (`Issue`, `read_issues`, `format_issue_frontmatter`,
  token-shape validation)
- `issuekit/workflow.py` (`claim_next`, `submit_for_review`, `request_changes`,
  `_write_active_issue`)
- `issuekit/commands/complete.py` (`complete_issue`)
- Issue #22 (assignable reviewer; depends on this implementer field + guard)
