---
id: 3
status: completed
priority: high
created: 2026-05-28
completed: 2026-05-29
title: Implement info and validate commands
---

# Issue #3: Implement info and validate commands

## Problem

The `info` and `validate` handlers are stubs (Issue #1). They need the core
library (Issue #2) to report status and enforce the tracker rules.

## Proposed Solution

Implement both commands by porting `../mine-js-monorepo/scripts/issues-info.mjs`
and `../mine-js-monorepo/scripts/issues-validate.mjs`.

## Impact

- New: `issuekit/commands/info.py`
- New: `issuekit/commands/validate.py`
- New: `tests/test_info.py`, `tests/test_validate.py`

## Implementation Plan

1. `info`: print active/completed counts, next id, and active issue list.
   Support `--json` for machine-readable output (counts, next_id, active list).
2. `validate`: implement every check listed in `docs/issues/README.md`
   "Validation Rules", matching `issues-validate.mjs`:
   - filename starts with numeric id; ids unique across active/completed
   - generated indexes exist, no unexpected index files, content matches a fresh
     `build_index_files`, and each contains the generated-file marker
   - frontmatter id matches filename; status/priority are allowed ASCII values;
     `created` and `title` present
   - completed issues use `status: completed`; active issues do not
   - frontmatter has no likely mojibake
   - issue ids >= `ascii_id_threshold` are ASCII-only
3. `validate` exits non-zero with a clear per-failure report; zero on success.

## Test Plan

- `uv run pytest tests/test_info.py tests/test_validate.py`.
- Cover: clean tree passes; duplicate id fails; stale index fails; bad frontmatter
  status fails; non-ASCII id-over-threshold fails; `--json` shape for info.

## Related Resources

- `../mine-js-monorepo/scripts/issues-info.mjs`
- `../mine-js-monorepo/scripts/issues-validate.mjs`
- Depends on Issue #2.

## Completion Notes

Summary: Implemented the `info` and `validate` command handlers, wired them
into the CLI dispatcher, and added command-level tests.

Verification:

- `uv run pytest tests/test_info.py tests/test_validate.py tests/test_cli.py`
- `uv run issuekit info --json`
