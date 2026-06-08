---
id: 57
status: completed
priority: medium
created: 2026-06-09
completed: 2026-06-09
stage: done
author: claude
title: Reduce redundant tracker reads and optimize implement-run snapshots and index writes
---

# Issue #57: Reduce redundant tracker reads and optimize implement-run snapshots and index writes

## Problem

Several hot paths re-read the whole tracker from disk more often than needed.
For a small tracker this is invisible, but cost grows linearly with the number
of completed issues (currently ~50 and only increasing).

1. Redundant directory scans per mutation. `workflow.claim_next` reads
   `active/`, then `_write_active_issue` parses and calls `_find_active_issue`,
   which reads `active/` again, so a single claim scans the active directory
   two to three times. The same re-read-after-write pattern appears in
   `claim_issue`, `submit_for_review`, and `request_changes`.
2. `read_all_issues` always reads BOTH `active/` and `completed/`, even when the
   caller only needs active issues. `author`, `complete`, and `approve` call
   `read_all_issues` and then immediately discard the completed list (or only
   need the next id / an active lookup). Reading every completed file's bytes on
   every author/complete/approve is wasteful.
3. `agents/runner._TrackerSnapshot.capture` reads EVERY file under
   `docs/issues/` (all completed issues and all indexes) into memory as bytes on
   every `issuekit implement` run, and `restore` re-walks and re-reads them. The
   guard only needs to detect and revert implementer mutations; snapshotting the
   entire completed history is the expensive way to do it.
4. `generate_indexes.write_index_files` (invoked by `_refresh_indexes` after
   every MCP mutation and most CLI mutations) deletes and rewrites ALL index
   files every time, even files whose content did not change.

## Proposed Solution

Pick the low-risk wins; correctness must not regress.

1. Let the workflow write helpers return the freshly written `Issue` (or accept
   an already-read issue list) so the post-write `read_issues` re-scan can be
   avoided, or at least de-duplicate scans within a single locked operation.
2. Add a lighter read path (e.g. `read_active_issues` / pass a `directories`
   selector) so callers that only need active issues do not pay to read all of
   `completed/`. Compute "next id" from a cheaper source where possible.
3. Scope `_TrackerSnapshot` to the subtree the implementer might touch (at
   minimum skip re-reading unchanged files; ideally snapshot via mtime/size or
   git status rather than full byte copies of all completed issues). Preserve
   the existing revert-and-warn guarantee from #52.
4. Make index writing incremental: compute expected files, write only the ones
   whose content changed, and remove only obsolete ones, instead of unlink-all
   then rewrite-all.

## Impact

- `issuekit/workflow.py` (claim/submit/request write paths)
- `issuekit/core.py` (`read_all_issues` / new selective reader, next-id helper)
- `issuekit/agents/runner.py` (`_TrackerSnapshot`)
- `issuekit/commands/generate_indexes.py` (`write_index_files`)
- Callers in `commands/*` and `mcp/server.py` that rely on current signatures.

## Implementation Plan

1. Measure first: add a quick benchmark or count of `read_issues` calls per
   operation to confirm the hotspots before changing behavior.
2. De-duplicate scans in the workflow lock blocks; have write helpers reuse the
   in-memory issue rather than re-reading.
3. Introduce a selective reader and update `author`/`complete`/`approve` to use
   it where they do not need completed issues.
4. Rework `_TrackerSnapshot` to avoid full byte copies while keeping the #52
   revert-and-warn behavior; add a regression test mirroring #52.
5. Make `write_index_files` write only changed/removed files; keep output
   byte-identical to today.

## Test Plan

- `uv run pytest tests/test_workflow.py tests/test_core.py tests/test_agents_runner.py tests/test_generate_indexes.py tests/test_implement_command.py`
- Add/keep a regression test for the #52 tracker-mutation revert path.
- `uv run pytest`
- `uv run issuekit validate` (indexes must remain byte-identical)

## Related Resources

- `issuekit/workflow.py`, `issuekit/core.py` `read_all_issues`,
  `issuekit/agents/runner.py` `_TrackerSnapshot`,
  `issuekit/commands/generate_indexes.py`
- Builds on #52 (tracker-mutation guard) and #49-#51 (run visibility).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-09

## Completion Notes

- Reduce redundant tracker reads and optimize implement-run snapshots and index writes.
- Verification: `Reviewed diff: core gained read_active_issues/read_completed_issues/read_issues_in_directories; callers (workflow, approve, complete, implement, mcp) now read only the directory they need (get_issue does active-then-completed short-circuit, preserving precedence). workflow write helpers de-duplicate the post-write re-scan: _write_active_issue builds the updated Issue in memory via _build_updated_issue and _find_active_issue accepts a preloaded list. write_index_files is now incremental (write only changed, remove only obsolete) instead of unlink-all/rewrite-all. _TrackerSnapshot keeps full-byte capture in non-git trees but caches only active/indexes bytes in a git repo and reverts completed/ via git restore, preserving the #52 revert-and-warn guarantee (test_implement_command_restores_agent_tracker_mutations passes). Full suite 282 passed/22 skipped; check-encoding clean; validate clean; indexes byte-identical.`
