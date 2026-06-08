---
id: 41
status: completed
priority: medium
created: 2026-06-08
completed: 2026-06-08
stage: done
title: Surface agent run status so humans can see running vs done
---

# Issue #41: Surface agent run status so humans can see running vs done

## Problem

When Claude hands work to codex or kimi through the agent runner (#37) and the
`issuekit implement` command (#39), there is no way for a human to observe
whether an agent is currently running or has finished. The runner writes
per-run logs under `.agent-runs/{stamp}.out.log` and `.agent-runs/{stamp}.err.log`,
but nothing records run state, so the only signals today are "the implement
command is still blocking" or "a log file stopped growing". Both codex and kimi
go through the same runner, so a single status layer can cover both.

## Proposed Solution

Have the runner write one status file per run as the single source of truth, and
add a read-only command to list and inspect runs. Because the status lives on
disk, a human in a second terminal (or an MCP caller) can see "running" live
even while `issuekit implement` blocks the driving session.

Status record at `.agent-runs/{stamp}.status.json`:

```json
{
  "run_id": "20260608-111012",
  "agent": "codex",
  "issue": 39,
  "status": "running",
  "pid": 12345,
  "started_at": "2026-06-08T11:10:12",
  "ended_at": null,
  "elapsed_sec": null,
  "exit_code": null,
  "plan": "docs/issues/active/039_implement_cli_command.md",
  "stdout_log": ".agent-runs/20260608-111012.out.log",
  "stderr_log": ".agent-runs/20260608-111012.err.log"
}
```

`status` is one of `running`, `completed`, `failed`, `timed_out`.

1. The runner writes the status file with `status: running` before launching the
   subprocess, then rewrites it with the terminal status, `ended_at`,
   `elapsed_sec`, and `exit_code` after the process finishes. Use an atomic
   temp-file-plus-rename write so a reader never sees a partial file.
2. Add `issuekit runs` to list runs newest-first as a table
   (run id, agent, issue, status, elapsed), with `--active` to show only
   in-progress runs and `--json` for structured output.
3. Add `issuekit runs <run-id>` to print one run's full record plus the tail of
   its stdout/stderr logs.

One status file per run (not a shared file) keeps concurrent codex and kimi runs
independent and lists them all without write contention.

## Impact

- `issuekit/agents/runner.py`: write and update the per-run status file around
  the subprocess lifecycle; thread the agent name and optional issue id into the
  record (extend `AgentRunner.run` inputs as needed without breaking callers).
- `issuekit/commands/runs.py` (new): read `.agent-runs/*.status.json` for the
  list and detail views.
- `issuekit/cli.py`: register the `runs` subcommand.
- `tests/`: runner writes `running` then a terminal status; `runs` lists and
  filters; `runs <id>` shows a record.

## Implementation Plan

1. Define a small status writer/reader helper (dataclass plus atomic JSON write)
   used by both the runner and the `runs` command so the schema has one owner.
2. Update `AgentRunner.run` to emit `running` before `Popen` and the terminal
   status after `wait`, including on timeout (`timed_out`) and non-zero exit
   (`failed`).
3. Add `issuekit/commands/runs.py` and register `runs` in `issuekit/cli.py`.
4. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Optional Follow-ups (out of core scope)

- Stale detection: if `status` is `running` but `pid` is no longer alive, the
  `runs` view marks the run `stale` so a crashed run is not shown as active.
- An MCP `list_runs` tool that reuses the same reader so Claude can report run
  state in chat, mirroring the propose CLI/MCP parity pattern.

## Test Plan

- `uv run pytest tests/test_runs_command.py`
- Manual: start `issuekit implement <id> --agent codex` in one terminal, run
  `issuekit runs --active` in another and confirm the run shows `running`, then
  `completed` after it finishes.
- `uv run issuekit validate`

## Related Resources

- Issue #37 (headless agent runner; writes the per-run logs)
- Issue #38 (agent registry; codex and kimi share the runner)
- Issue #39 (the implement command that drives runs)
- Issue #40 (review-gate integration; separate concern from run visibility)
- `issuekit/agents/runner.py`, `issuekit/commands/`, `issuekit/cli.py`

**Completed**: 2026-06-08

## Completion Notes

- Add agent run status visibility: runner writes one status JSON per run under .agent-runs (running -> completed/failed/timed_out) with atomic writes and O_EXCL run-id reservation; new issuekit runs command lists and inspects runs. Implemented by codex via issuekit implement (dogfooding #39), reviewed and approved by claude.
- Verification: `uv run pytest: 228 passed, 20 skipped; uv run issuekit validate: 0 warnings; uv run issuekit check-encoding: passed; issuekit runs/--active/--json/runs <id> smoke ok.`
