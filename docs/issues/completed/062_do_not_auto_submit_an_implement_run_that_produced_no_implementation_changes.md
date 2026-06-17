---
id: 62
status: completed
priority: high
created: 2026-06-17
completed: 2026-06-17
stage: done
author: claude
origin: mine-py#0@54e30f0e
title: Do not auto-submit an implement run that produced no implementation changes
---

# Issue #62: Do not auto-submit an implement run that produced no implementation changes

## Problem

On `issuekit implement <id> --agent <agent>`, the run is submitted for review
whenever the agent process exits 0, regardless of whether it changed any
implementation files. A codex run blocked by the execution environment (for
example a bwrap loopback failure on a host without working bubblewrap) exits 0,
makes zero file changes, yet `issuekit implement` advances the issue to
`stage=review`. The reviewer then sees an empty diff masquerading as a completed
implementation, and the issue is wedged in the review pool.

Reference: `issuekit/commands/implement.py` (around lines 95-129). `exit_code !=
0` short-circuits before submit, but the bwrap-block case exits 0; after the
mojibake gate and the heavy-deletion warning, `submit_for_review` is called
unconditionally. There is no "zero implementation changes" gate.

## Proposed Solution

Before `submit_for_review`, detect when the run touched zero non-tracker files
and refuse to submit. Keep the issue in implementation with a clear failure
message instead of advancing it to review.

- Reuse existing helpers: `_touched_paths(cwd)` already enumerates working-tree
  changes via `git status --short --untracked-files=all`, and
  `_is_under_issues_dir(path, issues_dir)` filters tracker churn under
  `docs/issues/`. Count files that are NOT under the issues dir.
- If that count is zero AND the run exited 0, do NOT call `submit_for_review`.
  Print a clear message (the agent produced no implementation changes; the issue
  remains claimed in implementation) and return non-zero.
- This is agent-independent and sandbox-independent; it protects every agent.

Decisions to lock in:

- Run the gate only on the `exit_code == 0`, not-timed-out path. `timed_out`
  already returns 124 and a non-zero exit already returns the code, so neither
  reaches submit today.
- When the repo is not a git work tree, `_touched_paths` returns `()`; we cannot
  tell what changed, so fall through to current behavior (submit) rather than
  block. This mirrors how `_warn_heavy_deletions` and `_mojibake_touched_files`
  already bail when not at a git root, and avoids regressing non-git repos.
- A genuinely no-op but valid implementation is rare; if one occurs the operator
  can drive `submit-review` manually. Do not add a `--force` flag in this issue;
  keep scope tight.
- Optional and OUT of required scope: also treating a known "blocked before any
  command ran" agent signal as non-success. The zero-change gate already covers
  the reported repro.

## Impact

- `issuekit/commands/implement.py` (submit path)
- Affects all `issuekit implement` runs: blocked/no-op runs no longer reach
  review.

## Implementation Plan

1. In `implement.py`, on the `exit_code == 0` path, compute the count of touched
   files that are NOT under the issues dir, using `_touched_paths(cwd)` and
   `_is_under_issues_dir(path, issues_dir)`.
2. When the git work tree is present and that count is zero, print a clear "no
   implementation changes; not submitting for review" message and return
   non-zero WITHOUT calling `submit_for_review`. The issue stays claimed in
   implementation.
3. Keep existing mojibake-gate and heavy-deletion-warning behavior. The no-op
   gate may run first; a zero-change tree trivially passes those anyway.

## Test Plan

- `uv run python -m pytest` (full suite) passes (use `uv run python`).
- New tests:
  - A run that exits 0 but leaves only `docs/issues/` (tracker) churn does NOT
    submit and returns non-zero; the issue is not advanced to review.
  - A run that touches at least one non-tracker file still submits as today.
  - Non-git working tree: behavior unchanged (still submits) so non-git repos do
    not regress.

## Related Resources

- Origin: `mine-py#0@54e30f0e`
- `issuekit/commands/implement.py` (`_touched_paths`, `_is_under_issues_dir`,
  submit path)
- Sibling scope: per-agent config merge that enables a sandbox override
  (separate issue).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-17

## Completion Notes

- Approved by claude.
- Verification: `Approved. codex implementation of #62 is correct and well-scoped.

Implementation (issuekit/commands/implement.py):
- New no-op gate after the unstaged-changes warning and before submit: when `_git_root(cwd) == cwd.resolve()` AND `_touched_implementation_paths(cwd, issues_dir)` is empty, prints an ERROR and returns 1 without calling submit_for_review; the issue stays claimed at stage=implementing. Matches the spec.
- Correctly distinguishes "git repo with zero non-tracker changes" (block) from "not a git repo" (`_touched_paths` returns () in both cases, so the explicit git-root check is required and present) -> non-git repos fall through and still submit.
- Added helper `_touched_implementation_paths` (touched paths minus docs/issues) and refactored `_mojibake_touched_files` to reuse it (DRY); behavior preserved.

Tests (tests/test_implement_command.py):
- tracker-only git changes -> exit 1, error on stderr, issue stays implementing.
- non-tracker code change -> submits, stage=review.
- mojibake-on-tracker-path is still ignored by the mojibake gate when a clean code file is also present (preserves the original coverage, now past the no-op gate).
- Non-git fallback (still submits) is covered by the pre-existing test_implement_command_resolves_issue_and_invokes_runner (no git init, reaches stage=review).

Verification:
- Full suite: 313 passed, 22 skipped (uv run python -m pytest).
- check-encoding clean; implement.py and test_implement_command.py are LF/no-BOM.

Out-of-scope items intentionally deferred per the issue (no --force flag; no "blocked before any command" agent signal). Sibling issue #63 (config merge) remains.`
