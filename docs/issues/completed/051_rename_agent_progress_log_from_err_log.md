---
id: 51
status: completed
priority: low
created: 2026-06-08
completed: 2026-06-08
stage: done
origin: py_cr_wrapper#0@3f071a8
title: Rename agent progress log from err.log to a non-error name
---

# Issue #51: Rename agent progress log from err.log to a non-error name

## Problem

The headless runner writes the agent stderr stream to
`.agent-runs/<run>.err.log` (`issuekit/agents/runner.py`). Many agents emit
ordinary progress notes (status, test output, reasoning) on stderr, so the file
named `err.log` usually holds normal activity, not errors. Operators who tail
`err.log` to follow progress see a filename that implies something went wrong,
which is misleading.

This issue covers proposal point 5. Points 1-3 and 6 are tracked in #49 and
point 4 in #50.

## Proposed Solution

- Rename the agent activity stream from `<run>.err.log` to a non-error name such
  as `<run>.agent.log` (or `<run>.trace.log`). Pick one name and use it
  consistently.
- Keep the stdout stream file as-is (or align its name in the same scheme, for
  example `<run>.out.log` stays, while stderr becomes `<run>.agent.log`).
- Preserve the ability to tell real failures apart: a failed run is already
  signalled by exit code and the `failed` / `timed_out` status in the run status
  JSON, so the stream rename does not lose error signal. If a separate
  hard-error capture is still wanted, document where it lives.
- Update every reader of the old name so nothing breaks:
  - `RunStatus.stderr_log` and `write_status` (`issuekit/agents/status.py`).
  - `issuekit/commands/implement.py` output (`stderr_log=...`).
  - `issuekit/commands/runs.py` listing.
  - Heartbeat/last-log tracking added in #49 (coordinate naming).
- Decide and document backward compatibility for pre-existing `.agent-runs/`
  directories that still contain `*.err.log` (read old names if present, write
  the new name going forward).

## Impact

- `issuekit/agents/runner.py`: stderr log path naming.
- `issuekit/agents/status.py`: status field name/value for the renamed log.
- `issuekit/commands/implement.py`, `issuekit/commands/runs.py`: printed paths
  and listings.
- Docs that reference `.err.log` (README / issue history are descriptive only;
  update user-facing references).

## Implementation Plan

1. Choose the new stream name (for example `agent.log`) and update the runner.
2. Update `RunStatus` field naming/value and all writers/readers.
3. Update `implement` and `runs` output to print the new path.
4. Add backward-compatible read of legacy `*.err.log` if a run dir predates the
   change.
5. Coordinate with #49 so the heartbeat tails the renamed file.

## Test Plan

- `uv run pytest tests/test_agents_runner.py tests/test_implement_command.py tests/test_runs_command.py`
- Unit test: a run writes the new `<run>.agent.log` (not `<run>.err.log`) and the
  status JSON / command output reference it.
- Unit test: `runs` still reads a legacy directory containing `*.err.log`.
- `issuekit validate`

## Related Resources

- Origin: `py_cr_wrapper#0@3f071a8`
- Split siblings: #49 (progress visibility), #50 (info status+stage)
- Coordinate with #49 (heartbeat tails the renamed log).

## Handoff

- Summary: Implemented by kimi via issuekit implement.

**Completed**: 2026-06-08

## Completion Notes

- Rename agent stderr log .err.log -> .agent.log with backward-compatible reads. err.log->agent.log done across runner/status/implement/runs + tests.
- Verification: `uv run pytest tests/test_agents_runner.py tests/test_implement_command.py tests/test_runs_command.py (26 passed); issuekit validate (0 warnings). Reviewer cleanup: removed dead/buggy with_suffix legacy fallback in runs.py (legacy handled by RunStatus.from_dict), renamed err_f->log_f in runner.py.`
