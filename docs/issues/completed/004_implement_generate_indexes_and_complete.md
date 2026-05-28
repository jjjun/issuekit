---
id: 4
status: completed
priority: high
created: 2026-05-28
completed: 2026-05-29
title: Implement generate-indexes and complete commands
---


# Issue #4: Implement generate-indexes and complete commands

## Problem

The `generate-indexes` and `complete` handlers are stubs (Issue #1). These are
the two write commands that mutate the tracker.

## Proposed Solution

Port `../mine-js-monorepo/scripts/issues-generate-indexes.mjs` and
`../mine-js-monorepo/scripts/issues-complete.mjs` using the core library.

## Impact

- New: `issuekit/commands/generate_indexes.py`
- New: `issuekit/commands/complete.py`
- New: `tests/test_generate_indexes.py`, `tests/test_complete.py`

## Implementation Plan

1. `generate-indexes`: call `build_index_files`, write every file under
   `docs/issues/indexes/`, and remove any stale index files not in the fresh set.
   Write UTF-8 without BOM and LF newlines.
2. `complete <id>`:
   - find the active issue file by id; error if not found or already completed
   - set frontmatter `status: completed` and `completed: <today>`
   - append a completion note with `--summary` and `--verification`
   - move the file from `active/` to `completed/`
   - run generate-indexes, then validate; fail loudly if validate fails
3. Require `--summary` and `--verification` to be ASCII (reject otherwise).
4. Both commands must produce no CRLF and no BOM in any written file.

## Test Plan

- `uv run pytest tests/test_generate_indexes.py tests/test_complete.py`.
- Cover: indexes written and stale ones removed; complete moves file, updates
  frontmatter, appends notes, regenerates indexes; completing a missing id fails;
  non-ASCII summary is rejected.
- Byte-level assertion that written files start without BOM and contain no CRLF.

## Related Resources

- `../mine-js-monorepo/scripts/issues-generate-indexes.mjs`
- `../mine-js-monorepo/scripts/issues-complete.mjs`
- Depends on Issue #2 and Issue #3 (validate).

**Completed**: 2026-05-29

## Completion Notes

- Implemented generate-indexes and complete commands.
- Verification: `uv run pytest tests/test_generate_indexes.py tests/test_complete.py tests/test_validate.py tests/test_cli.py`
