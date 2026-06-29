---
id: 69
status: completed
priority: medium
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: migrate-to-api: coerce empty optional enum fields to valid defaults before import
---

# Issue #69: migrate-to-api: coerce empty optional enum fields to valid defaults before import

## Problem

`issuekit migrate-to-api` (issuekit/commands/migrate_to_api.py, added in #68)
builds each import item with `"stage": data.get("stage") or issue.stage`. For a
legacy issue whose frontmatter has no `stage` line (or an empty one), this sends
`stage = ""`. The mine-py import schema `IssueImportItem`
(mine-py/src/domains/issues/schemas/issue.py) types `stage`, `status`, and
`priority` as enums, so an empty string fails Pydantic validation and the whole
import request returns HTTP 422.

The same hazard applies to `status` and `priority` if a legacy file ever omits
them, though `_issue_payload` currently falls back to `issue.issue_status` /
`"medium"` for those. `stage` has no such default and is the realistic failure
case (the frontmatter writer omits `stage` when empty, so older or
hand-edited files can lack it).

This only surfaces at real migration time against the live server, but it would
abort the entire import, so it should be fixed before the operator runs the
cutover.

## Proposed Solution

In `_issue_payload` (issuekit/commands/migrate_to_api.py), coerce empty optional
enum fields to their schema defaults before sending, or omit them so the server
applies its own default:

- `stage`: when empty, send `"todo"` (matches IssueImportItem default) or omit
  the key.
- Keep `status` defaulting to a valid value (currently issue.issue_status; if
  that can be empty, default to `"active"`).
- Keep `priority` defaulting to `"medium"`.

Prefer sending explicit valid defaults over omitting, so the imported issue is
deterministic and does not depend on server-side defaulting.

## Impact

- issuekit/commands/migrate_to_api.py (`_issue_payload` only).
- No change to the server; this aligns the client payload with the existing
  IssueImportItem contract.

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- Add a test in tests/test_migrate_to_api.py: a source issue file with no
  `stage` frontmatter produces an import payload whose `stage` is a valid enum
  value (e.g. `"todo"`), not `""`.
- Confirm the existing build_import_payload / dry-run tests still pass.

## Related Resources

- Origin: review of #68 (API migration phase 3); noted in the #68 approval.
- issuekit/commands/migrate_to_api.py (`_issue_payload`).
- mine-py/src/domains/issues/schemas/issue.py (`IssueImportItem`).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (317 passed, 23 skipped via uv run python -m pytest; collection 339 = prior HEAD 338 + the one new test, confirming no silent test loss); issuekit check-encoding clean. Reviewed: migrate_to_api._issue_payload now coerces status/priority/stage through a new _first_non_empty helper to valid defaults (active/medium/todo) instead of sending empty strings, so a legacy issue lacking a stage no longer trips the server IssueImportItem enum (the HTTP 422 hazard from the #68 review). New test test_build_import_payload_defaults_missing_stage_to_valid_enum asserts a stage-less source issue yields stage='todo'. Scope is limited to issuekit/commands/migrate_to_api.py and its test; no unrelated tracker files touched. Server unchanged.`
