---
id: 75
status: completed
priority: high
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: Fetch completed issues explicitly (server list excludes completed by default)
---

# Issue #75: Fetch completed issues explicitly (server list excludes completed by default)

## Problem

The mine-py list endpoint EXCLUDES completed issues when no `status` filter is
given (verified live: GET `/issues` returns non-completed only; GET
`/issues?status=completed` returns the completed ones). Two client code paths
wrongly assume an unfiltered list returns EVERYTHING:

1. `migrate-to-api` verification: `migrate_to_api.run` calls
   `client.list_issues()` (no status) and passes it to `verify_import`. Because
   the migrated issues are completed, the unfiltered list returns 0 of them and
   verification fails with "Imported issue id(s) missing from server list:
   1..N" even though the import SUCCEEDED. (Confirmed: all 74 issuekit issues are
   present on the server under status=completed; the migration data is intact.)

2. `ApiStore.read_all_issues` (issuekit/store.py): it does
   `all_issues = self._list_issues()` (no status) and splits that into active
   vs completed. Since the unfiltered list omits completed issues, the completed
   bucket is always empty. This powers `issuekit info`, so info under API mode
   shows no completed issues.

## Proposed Solution

Where the client needs ALL issues, query the non-completed default AND
`status=completed`, then union them.

1. `ApiStore` (issuekit/store.py):
   - `read_completed_issues`: keep using `_list_issues(status="completed")`
     (already correct).
   - `read_active_issues`: keep `_list_issues()` (server default already returns
     non-completed; the existing `!= "completed"` filter is fine as defense).
   - `read_all_issues`: build it from the two explicit queries instead of one
     unfiltered list, e.g. `active = self.read_active_issues()`,
     `completed = self.read_completed_issues()`,
     `all = sorted(active + completed, key=id, relative_path)`; return
     `(active, completed, all)`. Do NOT rely on a single unfiltered list to
     contain completed issues.

2. `migrate_to_api.run` (issuekit/commands/migrate_to_api.py): gather the
   server side as the union of non-completed and completed before verifying,
   e.g. `server_issues = client.list_issues() + client.list_issues(status="completed")`.
   Keep `verify_import` logic otherwise unchanged (it already dedupes via sets).

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- Make `FakeIssuekitClient.list_issues` emulate the server contract: when
  `status` is None, EXCLUDE issues whose status is "completed"; when
  `status="completed"`, return only completed. This is required so the tests
  below are meaningful (today the fake likely returns everything).
- New/updated tests:
  - `ApiStore.read_all_issues` returns completed issues even though the default
    (status=None) list excludes them (seed the fake with a completed issue and
    assert it appears in both the completed bucket and the combined list).
  - `ApiStore.read_active_issues` still excludes completed.
  - `migrate-to-api` verification passes when all imported issues are completed
    (drive run() against a fake whose default list excludes completed; assert
    success, not the "missing from server list" error).

## Out of scope

- Changing the server's default-excludes-completed behavior (it is a reasonable
  queue default; the client should ask for completed explicitly).

## Related Resources

- issuekit/store.py (`ApiStore.read_all_issues`, `_list_issues`).
- issuekit/commands/migrate_to_api.py (`run`, `verify_import`).
- issuekit/testing.py (`FakeIssuekitClient.list_issues`).
- mine-py list endpoint default filter (excludes completed).
- Found during the real migration (after #74); the data import itself is
  correct (74/74 present).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (341 passed, 25 skipped via uv run python -m pytest); issuekit check-encoding clean. Reviewed: the client's fetch-all logic now accounts for the server excluding completed issues by default. ApiStore.read_all_issues is rebuilt from read_active_issues() + read_completed_issues() (the latter uses status=completed) and sorted, instead of splitting a single unfiltered list (which omitted completed). migrate_to_api.run now verifies against client.list_issues() + client.list_issues(status='completed'). Crucially FakeIssuekitClient.list_issues was updated to emulate the real contract (status=None excludes completed; status=completed returns only completed), so the new tests are meaningful: tests assert read_all_issues surfaces completed issues, read_active excludes them, and migrate verification passes when all imported issues are completed. Scope limited to store.py, migrate_to_api.py, testing.py and their tests. Live-confirmed the underlying data is already correct on prod (74/74 issuekit issues present under status=completed); this fixes the false-negative verification and API-mode info.`
