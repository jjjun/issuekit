---
id: 55
status: active
priority: medium
created: 2026-06-09
completed: 
stage: todo
author: claude
title: Add shared issue-lookup and id-parse helpers and consolidate index-diff logic
---

# Issue #55: Add shared issue-lookup and id-parse helpers and consolidate index-diff logic

## Problem

Two small patterns are copy-pasted across many command modules, and one
non-trivial computation (index drift detection) is implemented three times with
subtly different shapes.

1. "Find an issue by id" is written inline at six sites:
   `workflow._find_active_issue`, `approve._resolve_approval_context`,
   `complete.complete_issue`, `propose._find_issue`, `implement.run`, and
   `mcp/server.get_issue`. Each is `next((c for c in <issues> if c.id == id),
   None)`.
2. CLI id parsing (`int(args.id)` with a "Invalid issue id" error and return 1)
   is duplicated in `complete.run`, `approve.run`, `implement.run`, and both
   handlers in `handoff.py`.
3. Index drift detection (compute expected indexes, then determine
   missing/extra/stale by reading each index file with `utf-8-sig` and comparing
   content) is implemented separately in `commands/info._stale_indexes`,
   `commands/validate.run`, and `commands/setup._add_index_actions`.

## Proposed Solution

1. Add `find_issue_by_id(issues, issue_id) -> Issue | None` to `issuekit/core.py`
   and use it at the six sites.
2. Add a small CLI helper (e.g. `parse_issue_id_arg(raw) -> int` raising a
   shared error, or a helper that prints and returns the exit code) and apply it
   in the four CLI handlers.
3. Add a shared index-diff helper to `core.py`, for example
   `diff_index_files(issues_dir, expected) -> IndexDiff(missing, extra, stale)`,
   and have `info`, `validate`, and `setup` consume it instead of re-reading and
   re-comparing index files independently.

## Impact

- `issuekit/core.py` (new helpers)
- `issuekit/workflow.py`, `issuekit/commands/{approve,complete,propose,implement}.py`,
  `issuekit/mcp/server.py` (issue lookup)
- `issuekit/commands/{complete,approve,implement,handoff}.py` (id parsing)
- `issuekit/commands/{info,validate,setup}.py` (index diff)

## Implementation Plan

1. Implement and unit-test `find_issue_by_id`; replace the six inline lookups.
2. Implement the CLI id-parse helper; apply it to the four handlers, preserving
   the existing stderr message and exit code 1.
3. Implement `diff_index_files` (or equivalent) returning missing/extra/stale;
   route `info`, `validate`, and `setup` through it. Keep their current output
   wording.
4. Confirm no behavior change in CLI output and JSON payloads.

## Test Plan

- `uv run pytest tests/test_core.py tests/test_info.py tests/test_validate.py tests/test_setup.py tests/test_complete.py tests/test_approve_command.py tests/test_implement_command.py`
- `uv run pytest`
- `uv run issuekit validate`

## Related Resources

- `issuekit/core.py`, `issuekit/commands/info.py` `_stale_indexes`,
  `issuekit/commands/validate.py`, `issuekit/commands/setup.py`
  `_add_index_actions`
