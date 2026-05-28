---
id: 1
status: completed
priority: high
created: 2026-05-28
completed: 2026-05-29
title: Bootstrap package layout and CLI dispatcher
---

# Issue #1: Bootstrap package layout and CLI dispatcher

## Problem

The repo has scaffolding (pyproject, README, AGENTS, docs/issues) but no Python
implementation. The console script `issuekit = issuekit.cli:main` points to a
module that does not exist yet, so `uv run issuekit` fails.

## Proposed Solution

Create the package skeleton and an argparse-based CLI dispatcher that registers
every subcommand. Each subcommand handler is a stub that raises
`NotImplementedError` for now; later issues fill them in. `issuekit --help` and
`issuekit <cmd> --help` must work.

## Impact

- New: `issuekit/cli.py`
- New: `issuekit/commands/__init__.py`
- New: `tests/test_cli.py`

## Implementation Plan

1. Add `issuekit/cli.py` with `main(argv=None) -> int`.
2. Use `argparse` with subparsers for: `info`, `validate`, `generate-indexes`,
   `complete`, `check-encoding`, `init`.
3. Wire argument shapes now (do not implement logic):
   - `info` accepts `--json`
   - `complete` accepts positional `<id>`, `--summary`, `--verification`
   - `check-encoding` accepts `--json`
4. Each handler raises `NotImplementedError` with the command name.
5. `main` returns a non-zero exit code on unknown command, zero on `--help`.
6. Standard library only. No third-party deps.

## Test Plan

- `uv run issuekit --help` lists all six subcommands.
- `uv run pytest tests/test_cli.py` passes.
- `test_cli.py` asserts the parser registers all six subcommands and that
  `complete` requires an id.

## Related Resources

- `AGENTS.md` (target architecture)
- Dispatcher pattern reference: `../mine-js-monorepo/scripts/issues-*.mjs`
  (sibling checkout) each map to one subcommand.

## Completion Notes

Summary: Added the package CLI dispatcher, command package skeleton, and focused
CLI tests for the bootstrap command surface.

Verification:

- `uv run pytest`
- `uv run pytest tests/test_cli.py`
- `uv run issuekit --help`
- `uv run issuekit complete`
