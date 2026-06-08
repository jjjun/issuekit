---
id: 50
status: completed
priority: medium
created: 2026-06-08
completed: 2026-06-08
stage: done
origin: py_cr_wrapper#0@3f071a8
title: Show status and stage in info active issue listings
---

# Issue #50: Show status and stage in info active issue listings

## Problem

After `issuekit implement` submits an issue for review, the command prints
`submitted_review ... stage=review`, but `issuekit info` lists the same issue as
`[in_progress]`. The frontmatter has both `status: in_progress` and
`stage: review`, so each output is technically correct, but the queue view drops
the stage and looks inconsistent with the implement output.

The cause is in `issuekit/commands/info.py`: the active-issue listing emits only
`status` (`issue.issue_status or issue.status`) and never includes `stage`. So a
reader cannot tell from `info` that an `in_progress` issue is actually sitting in
the review stage.

This issue covers proposal point 4. Points 1-3 and 6 are tracked in #49 and
point 5 in #51.

## Proposed Solution

- In `issuekit/commands/info.py`, include `stage` alongside `status` in the
  active-issue listing when a stage is present, for example:
  `- #49: <title> [in_progress, stage=review] (<file>)`.
- When `stage` is empty/unset, keep the current `[status]` rendering so
  unstarted issues are not cluttered.
- Add `stage` to the `activeIssues` entries in the JSON summary so the structured
  output carries the same information as the human-readable output.
- Apply the same status+stage rendering to `issuekit queue` listings if they
  share the same gap, so the two views stay consistent.

## Impact

- `issuekit/commands/info.py`: active-issue text listing and the `activeIssues`
  JSON entries gain `stage`.
- `issuekit/commands/queue.py`: align listing format if affected.
- Pure display change; no lifecycle or frontmatter semantics change.

## Implementation Plan

1. Add `stage` to each `activeIssues` JSON entry in `info.py`.
2. Update the text listing to render `[status, stage=<stage>]` when a stage is
   set and `[status]` otherwise.
3. Check `queue.py` for the same pattern and align it.

## Test Plan

- `uv run pytest tests/test_info.py tests/test_queue.py`
- Unit test: an active issue with `status: in_progress` and `stage: review`
  renders `[in_progress, stage=review]` in text and carries `stage` in JSON.
- Unit test: an active issue with no stage still renders `[active]`.
- `issuekit validate`

## Related Resources

- Origin: `py_cr_wrapper#0@3f071a8`
- Split siblings: #49 (progress visibility), #51 (rename err.log)

## Handoff

- Summary: Implemented by kimi via issuekit implement.

**Completed**: 2026-06-08

## Completion Notes

- Show stage alongside status in info active-issue listings (#50: proposal point 4). Text and JSON both carry stage.
- Verification: `uv run pytest tests/test_info.py (10 passed). info.py adds stage to activeIssues JSON and renders [status, stage=X] when stage set, [status] otherwise. Confirmed queue.py already prints stage (line 29), so no gap there - correctly left unchanged. No tests/test_queue.py exists. No stray files.`
