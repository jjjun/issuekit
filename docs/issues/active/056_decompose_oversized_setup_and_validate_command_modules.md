---
id: 56
status: active
priority: low
created: 2026-06-09
completed: 
stage: todo
author: claude
title: Decompose oversized setup and validate command modules
---

# Issue #56: Decompose oversized setup and validate command modules

## Problem

Two modules have grown large enough that they are hard to read and test in
focused units.

1. `issuekit/commands/setup.py` (~464 lines) mixes three concerns in one file:
   the `run` entry point, the diagnostics collectors (`collect_diagnostics` and
   the six `_*_diagnostic` functions), and the setup-action collectors
   (`collect_setup_actions` and the many `_add_*_action` functions), plus their
   JSON/text printers. The per-file existence/merge checks in the action
   collectors also overlap conceptually with the file-writing logic in
   `commands/init.py`.
2. `issuekit/commands/validate.py` `run` is a single ~150-line function that
   inlines every validation rule (id presence, frontmatter id match, status,
   priority, assignee/implementer/author/stage tokens, mojibake, duplicate ids,
   index drift). It is hard to test a single rule in isolation.

## Proposed Solution

1. Split `setup.py` into a small package or sibling modules, e.g.
   `commands/setup/__init__.py` (run + printers), `setup/diagnostics.py`, and
   `setup/actions.py`. Keep the public `run`, `build_json_payload`,
   `build_check_json_payload`, `collect_diagnostics`, and `collect_setup_actions`
   names importable from `issuekit.commands.setup` so `mcp/server.py` and tests
   keep working.
2. Refactor `validate.run` into a list of small rule functions, each taking an
   `Issue` (or the issue set) and returning a list of error/warning strings, then
   have `run` aggregate them. Reuse the shared index-diff helper from the related
   consolidation issue.

## Impact

- `issuekit/commands/setup.py` -> package or split modules.
- `issuekit/commands/validate.py` -> rule functions + thin `run`.
- `issuekit/mcp/server.py` (imports `approve_issue`; confirm setup imports stay
  stable).
- Tests in `tests/test_setup.py` and `tests/test_validate.py` may import private
  helpers; keep import paths stable or update tests.

## Implementation Plan

1. Carve `setup.py` into diagnostics and actions modules; re-export the public
   surface from the package `__init__`. No behavior change.
2. Extract validate rules into named functions; `run` composes them. Preserve
   exact error/warning text and ordering so output and tests are unchanged.
3. Run the full suite to confirm no observable change.

## Test Plan

- `uv run pytest tests/test_setup.py tests/test_validate.py tests/test_mcp_server.py`
- `uv run pytest`
- `uv run issuekit validate`
- `uv run issuekit setup --check`

## Related Resources

- `issuekit/commands/setup.py`, `issuekit/commands/validate.py`,
  `issuekit/commands/init.py`
- Depends on / pairs with the index-diff consolidation issue.
