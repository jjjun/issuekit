---
id: 20
status: in_progress
priority: medium
created: 2026-06-01
completed:
assignee: claude
stage: review
title: Read issuekit config from a standalone issuekit.toml for non-Python repos
---

# Issue #20: Read issuekit config from a standalone issuekit.toml for non-Python repos

## Problem

`load_config` only reads `[tool.issuekit]` from `pyproject.toml`. Non-Python
repos that adopt issuekit have no `pyproject.toml`, so they cannot set any
config (`issues_dir`, `ascii_id_threshold`, `recent_count`, `assignees`,
`stages`). This blocks the mine-js-monorepo migration: it is a pnpm/JS monorepo
with no `pyproject.toml`, and it needs `ascii_id_threshold` set so its mix of
new ASCII issues and archived legacy issues validates correctly. Today such a
repo silently falls back to all defaults with no way to override.

## Proposed Solution

Let `load_config` read a standalone `issuekit.toml` at the repo root as an
alternative config source, so any repo (JS, Go, etc.) can configure issuekit
without a `pyproject.toml`. Keep `pyproject.toml`'s `[tool.issuekit]` working
unchanged. When both exist, define a clear precedence. The standalone file uses
the same keys, but at the top level (no `[tool.issuekit]` table nesting).

## Impact

- Modified: `issuekit/config.py` (`load_config` reads `issuekit.toml` too)
- New: `tests/test_config.py` cases for the standalone file and precedence
- Modified: `README.md` (document `issuekit.toml` for non-Python repos)
- Modified: `issuekit/commands/init.py` only if init should scaffold a sample
  `issuekit.toml` for non-Python repos (optional; see plan step 5)

## Implementation Plan

1. In `issuekit/config.py`, add a helper that returns the raw config dict from
   the first available source, in this precedence:
   - `pyproject.toml` `[tool.issuekit]` if that file exists AND contains the
     table (preserves current behavior for Python repos), else
   - `issuekit.toml` at the repo root, read at the top level (keys directly:
     `issues_dir = "docs/issues"`, `ascii_id_threshold = 407`, etc.).
   - else `{}` (all defaults).
   Document the precedence in a comment: pyproject's `[tool.issuekit]` wins when
   present so existing Python repos are unaffected.
2. Parse `issuekit.toml` with `tomllib`, reading with `encoding="utf-8-sig"` to
   match the existing `pyproject.toml` read. Reuse the same coercion
   (`int(...)`, `_string_tuple(...)`) so the two sources behave identically.
3. Keep the `IssuekitConfig` dataclass and its fields unchanged; only the source
   resolution changes. `load_config(cwd)` keeps the same signature and return
   type.
4. Guard against a malformed `issuekit.toml`: a TOML parse error should raise a
   clear error naming the file (do not silently fall back to defaults, which
   would hide a user typo).
5. Optional: if a repo has neither `pyproject.toml` nor `issuekit.toml`, leave
   behavior as defaults (do not force-create a file). Scaffolding a sample
   `issuekit.toml` from `init`/`setup` is out of scope here unless trivial; if
   added, gate it so it never overwrites an existing file.

## Test Plan

- `uv run pytest tests/test_config.py`
- Standalone only: a repo dir with `issuekit.toml` (no `pyproject.toml`) and
  `ascii_id_threshold = 407`, `issues_dir = "docs/issues"` loads those values.
- pyproject precedence: a dir with both files, where `pyproject.toml`
  `[tool.issuekit]` and `issuekit.toml` disagree, loads the pyproject values.
- issuekit.toml fallback: a dir with `pyproject.toml` that has NO
  `[tool.issuekit]` table plus an `issuekit.toml` loads the issuekit.toml values.
- Neither file: loads defaults (unchanged behavior).
- Malformed `issuekit.toml`: raises an error naming the file (not a silent
  default).
- `assignees`/`stages` lists load from `issuekit.toml` the same as from
  pyproject.
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/config.py` (`load_config`, `IssuekitConfig`, `_string_tuple`)
- `README.md`
- mine-js-monorepo migration (the JS monorepo that needs this; it will ship an
  `issuekit.toml` with a high `ascii_id_threshold` after archiving legacy issues)

## Handoff

- Summary: Implemented standalone issuekit.toml config loading with pyproject precedence, added config tests, and documented the standalone config file.
- Branch: `main`
- Commit: `1ffd2e6`
