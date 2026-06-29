---
id: 68
status: active
priority: medium
created: 2026-06-29
completed: 
stage: todo
author: claude
title: API migration phase 3: cutover, migration command, and doc updates
---

# Issue #68: API migration phase 3: cutover, migration command, and doc updates

Part of epic #64 (migrate issue storage to the mine-py API). This is phase 3
of 4: the irreversible cutover - migration, removing the local store, and docs.

## BLOCKED

Do not start until phase 2 (write path) is merged. This phase flips the default
to API-only and deletes the local tracker, so reads AND writes must already work
over the API.

## Goal

Import existing issues into mine-py, make the API the only store, remove the
filesystem tracker and everything that only existed to manage files, and update
all docs.

## Scope

1. Migration command `issuekit migrate-to-api`:
   - Read every legacy file under `docs/issues/{active,completed}/*.md`, parse
     frontmatter + body, and build the import payload: `number` (the id),
     `title`, `body`, `status`, `priority`, `stage`, `assignee`, `implementer`,
     `author`, `reviewer`, `created`, `completed`, `origin`, `extra` (passthrough
     frontmatter). Body should be the raw markdown WITHOUT the generated index
     wrappers.
   - POST to `/api/issues/{project}/issues/import` (admin token). The server
     upserts by `(project, number)` and bumps the counter to `max+1`, so the
     command is idempotent and re-runnable.
   - Verify: list the server issues and assert ids/counts match the source;
     print a summary. Support a `--dry-run` that builds and validates the payload
     without posting.
2. Flip to API-only:
   - Make `ApiStore` the default; remove the `FilesystemStore` fallback (or keep
     it only behind an explicit escape hatch). Remove the filesystem
     `claim_lock` entirely.
3. Retire file-only machinery:
   - `generate-indexes` and the `docs/issues/indexes/` tree.
   - File-based `validate` checks and `check-encoding` of issue files (the API is
     the source of truth; keep only the connectivity/well-formedness validate
     from phase 1).
   - The `agents/runner.py` `docs/issues/` snapshot+restore guard (issue #52):
     with no local tracker files, an implementer agent can no longer corrupt the
     tracker, so the guard is unnecessary - remove it and its tests.
4. Remove the repo tracker and update docs:
   - Delete `docs/issues/{active,completed,indexes}` from the repo (and the
     stale local copies). Decide the fate of `docs/issues/incoming/` - cross
     -project proposals are a separate, not-yet-migrated flow; either keep
     `incoming/` as-is for now or note it as a follow-up (do NOT silently drop
     it).
   - Replace `docs/issues/README.md` with an API-backed description.
   - Update `CLAUDE.md`, the `issuekit protocol` text, and the MCP server
     instructions/resources to describe the API-backed model (no local files,
     server-allocated ids).

## Cutover order (important)

Run `migrate-to-api` against the target mine-py and VERIFY before deleting any
local files. The deletion is the point of no return.

## Out of scope

- Migrating the cross-project proposal flow (`propose` / `incoming` / `adopt`)
  to the API. Track separately if desired.

## Test plan

- Exporter: unit-test parsing of legacy `.md` fixtures into the import payload
  (ids, dates, passthrough `extra`, body without index wrapper).
- `migrate-to-api --dry-run` builds a valid payload; the real run is idempotent
  against `FakeIssuekitClient` import.
- Retired commands are gone or error cleanly; the snapshot-guard removal does not
  break the implement flow.
- Full suite: `uv run python -m pytest`.
- Manual: run the migration against a staging/prod mine-py and confirm the list
  matches.

## Related

- Epic: #64. Depends on: phase 2.
- Server import: `POST /api/issues/{project}/issues/import`
  (`import_issues` + `set_counter_at_least`).
- Background: issue #52 (snapshot+restore guard being removed).
