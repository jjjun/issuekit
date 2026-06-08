---
id: 53
status: completed
priority: low
created: 2026-06-09
completed: 2026-06-09
stage: done
author: claude
title: Remove dead code and legacy agent-log compatibility
---

# Issue #53: Remove dead code and legacy agent-log compatibility

## Problem

Several pieces of code are now dead or only exist for backward compatibility
that the project no longer needs to keep:

1. `issuekit/cli.py` defines `_not_implemented(command_name)` but nothing ever
   references it. Every subcommand sets a real `func`, so this is dead code.
2. Legacy agent-log compatibility for the pre-#51 `.err.log` / `stderr_log`
   naming is still carried in three places:
   - `issuekit/agents/status.py` `RunStatus.from_dict` falls back to
     `data["stderr_log"]` when `agent_log` is absent.
   - `issuekit/agents/runner.py` `_reserve_run_id` still probes
     `f"{run_id}.err.log"` when reserving a run id.
   - `issuekit/commands/runs.py` carries a comment block explaining the legacy
     `.err.log` mapping.
   Issue #51 renamed the live log to `.agent.log`; old run-log directories are
   disposable gitignored storage, so this compatibility shim no longer earns
   its keep.
3. `issuekit/agents/adapters/codex.py` overrides `parse_output` with a body
   identical to the inherited `ConfigAgentAdapter.parse_output`
   (`{"stdout": stdout, "stderr": stderr}`). The override is redundant. (Note:
   `KimiAdapter.parse_output` is NOT redundant; it extracts `resume_session_id`,
   so leave it alone.)

## Proposed Solution

Remove the dead and obsolete-compat code:

1. Delete `_not_implemented` from `cli.py`.
2. Drop the `stderr_log` fallback in `RunStatus.from_dict` so `agent_log` is a
   required key; drop the `.err.log` probe in `_reserve_run_id`; remove the
   now-stale legacy comment in `runs.py`.
3. Delete the redundant `parse_output` override in `CodexAdapter` so it inherits
   the base implementation.

## Impact

- `issuekit/cli.py`
- `issuekit/agents/status.py`
- `issuekit/agents/runner.py`
- `issuekit/commands/runs.py`
- `issuekit/agents/adapters/codex.py`
- Tests that construct legacy status dicts with `stderr_log` (if any) must be
  updated to use `agent_log`.

## Implementation Plan

1. Remove `_not_implemented` and confirm no references remain.
2. Make `agent_log` a required key in `RunStatus.from_dict`; remove the
   `stderr_log` branch.
3. Remove the `.err.log` existence check in `_reserve_run_id` and the legacy
   comment in `runs.py`.
4. Remove `CodexAdapter.parse_output`.
5. Update or delete any test fixtures that depended on the legacy naming.

## Test Plan

- `uv run pytest tests/test_agents_runner.py tests/test_runs_command.py tests/test_cli.py tests/test_agents_registry.py`
- `uv run pytest`
- `uv run issuekit check-encoding`

## Related Resources

- #51 renamed the agent log from `.err.log` to `.agent.log`.
- `issuekit/agents/status.py`, `issuekit/agents/runner.py`,
  `issuekit/commands/runs.py`, `issuekit/cli.py`,
  `issuekit/agents/adapters/codex.py`

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-09

## Completion Notes

- Remove dead code and legacy agent-log compatibility.
- Verification: `Reviewed codex's diff: removed unused _not_implemented (cli.py), the redundant CodexAdapter.parse_output override, the .err.log probe in _reserve_run_id, the stderr_log fallback in RunStatus.from_dict, and the stale legacy comment in runs.py; test_runs renamed to agent_log. All correct. Reviewer fix applied: codex also dropped the still-required 'import argparse' from cli.py (breaking build_parser at runtime) and saved the file as CRLF; reviewer restored the import and normalized to LF. After the fix: uv run pytest -> 270 passed, 22 skipped; uv run issuekit check-encoding -> clean; issuekit CLI works.`
