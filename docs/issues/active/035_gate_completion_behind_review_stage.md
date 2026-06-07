---
id: 35
status: active
priority: high
created: 2026-06-08
completed:
title: Gate issue completion behind review stage
---

# Issue #35: Gate issue completion behind review stage

## Problem

An implementer can mark an issue completed without it ever passing a reviewer.

`complete_issue` (`issuekit/commands/complete.py:63`) closes an issue with no
check that it reached review. Its only guard, `ensure_not_self_review`
(`issuekit/workflow.py:272`), is a no-op unless `require_distinct_reviewer` is
true, and that flag defaults to false (`issuekit/config.py:20`). The
`issuekit complete <id>` CLI (`issuekit/cli.py:81-88`) calls `complete_issue`
directly, so any agent can close an issue while it is still at stage `todo` or
`implementing`, skipping `submit_for_review` and the reviewer's `approve`.

The intended flow is implementer -> `submit_for_review` -> reviewer `approve`
(`issuekit/mcp/server.py:129` calls `complete_issue` only after review), but
nothing enforces it. `docs/issues/README.md` even presents `issuekit complete`
as the default close path (README lines 23-27 and 203-206), which encourages the
bypass.

## Proposed Solution

Make completion require the issue to have passed review, with an explicit
escape hatch for legitimate direct closes.

- Add a config flag `require_review_before_complete` (default true) in
  `issuekit/config.py`.
- In `complete_issue`, accept `force: bool = False`. When
  `config.require_review_before_complete` is true and `force` is false, require
  `issue.stage == "review"` before completing; otherwise raise `WorkflowError`
  pointing at `submit_for_review` / `approve`. The `approve` path already leaves
  the issue at stage `review`, so reviewer-driven completion keeps working.
- Add a `--force` flag to the `complete` subparser in `issuekit/cli.py` and
  thread it through `complete.run` to `complete_issue`, for abandoned or trivial
  issues that close without review.
- Update `docs/issues/README.md` so completion is described as the reviewer's
  `approve`, with `issuekit complete --force` as the explicit non-review escape
  hatch rather than the default.

## Impact

- `issuekit/config.py`: new `require_review_before_complete` flag and parsing.
- `issuekit/commands/complete.py`: stage gate plus `force` parameter.
- `issuekit/cli.py`: `--force` on the `complete` subparser.
- `issuekit/mcp/server.py`: verify `approve` still completes review-stage issues
  (expected: no change needed).
- `docs/issues/README.md`: completion guidance.
- Tests under `tests/`.

## Implementation Plan

1. Add `require_review_before_complete` to `IssuekitConfig` and `load_config`
   with bool parsing (reuse `_bool_value`).
2. Add `force: bool = False` to `complete_issue`; raise `WorkflowError` when the
   gate is active, `force` is false, and `issue.stage != "review"`.
3. Add `--force` to the `complete` subparser and pass it through `complete.run`.
4. Confirm `approve` (`issuekit/mcp/server.py`) still completes review-stage
   issues; add or adjust tests.
5. Update the README close-path guidance.
6. Run tests, `issuekit validate`, and `issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_cli.py tests/test_mcp_server.py`
- Manual: claim an issue (stage `implementing`); `issuekit complete <id>` is
  rejected; `submit-review` then `approve` succeeds; `issuekit complete <id>
  --force` succeeds.
- `uv run issuekit validate`
- `uv run issuekit check-encoding`

## Related Resources

- `issuekit/commands/complete.py`
- `issuekit/cli.py`
- `issuekit/config.py`
- `issuekit/mcp/server.py` (`approve`)
- `docs/issues/README.md`
- `issuekit/workflow.py` (`ensure_not_self_review`, `require_distinct_reviewer`)
- Note: with `default_reviewer = auto` and `require_distinct_reviewer` false the
  review pool is open, so an implementer could still self-approve after submit.
  Tightening self-approval is out of scope here; see #21 and #23.
- Issue #34 (the author-role protocol; sibling fix for handoff discipline)
