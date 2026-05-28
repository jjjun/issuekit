---
id: 2
status: completed
priority: high
created: 2026-05-28
completed: 2026-05-29
title: Port core issue library and tool config
---

# Issue #2: Port core issue library and tool config

## Problem

All commands need shared logic: read issue files, parse frontmatter, compute the
next id, and build index file contents. The reference implementation is
`../mine-js-monorepo/scripts/issues-lib.mjs` (sibling checkout). Nothing exists
in Python yet.

## Proposed Solution

Port `issues-lib.mjs` to `issuekit/core.py` as an equivalent, well-tested module.
Keep behavior identical; do not redesign. Add `issuekit/config.py` to read
optional `[tool.issuekit]` overrides from the consuming repo's `pyproject.toml`.

## Impact

- New: `issuekit/core.py`
- New: `issuekit/config.py`
- New: `tests/test_core.py`, `tests/fixtures/`

## Implementation Plan

1. In `core.py`, port these from `issues-lib.mjs`:
   - `parse_issue_id(filename)` (regex `^(\d+)_.*\.md$`)
   - frontmatter parse (strip leading BOM, `---` delimited, simple `key: value`,
     strip surrounding quotes) and frontmatter format helpers
   - issue model: read `active/` and `completed/`, resolve id/title/status/
     priority/created/completed with frontmatter-first then legacy fallback
   - `get_next_issue_id`, `group_issues_by_id`
   - `build_index_files(active, completed)` -> dict of `{filename: content}`,
     including `active.md`, `completed-recent.md` (recent_count newest), and
     `completed-NNN-MMM.md` range pages
   - mojibake detection pattern (port the regex from `mojibakePattern`)
2. Use `pathlib` and synchronous file IO. Standard library only.
3. In `config.py`, read `[tool.issuekit]` via `tomllib`. Defaults:
   `recent_count=30`, `ascii_id_threshold=0`, `issues_dir="docs/issues"`.
4. Preserve the generated-file marker text used by the index pages so
   `validate` can detect generated files.

## Test Plan

- `tests/fixtures/` holds a small sample `docs/issues/` tree (active + completed,
  with and without frontmatter).
- `uv run pytest tests/test_core.py` covers: frontmatter parse incl. BOM strip,
  next id, index content for active/recent/range pages, mojibake detection.

## Related Resources

- `../mine-js-monorepo/scripts/issues-lib.mjs`
- `docs/issues/README.md` (frontmatter and index spec)

## Completion Notes

Summary: Ported the shared issue model, frontmatter parser/formatter, index
builder, mojibake detection, and `[tool.issuekit]` config loading.

Verification:

- `uv run pytest tests/test_core.py tests/test_cli.py`
