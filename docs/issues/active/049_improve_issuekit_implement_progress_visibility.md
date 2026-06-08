---
id: 49
status: active
priority: medium
created: 2026-06-08
completed:
origin: py_cr_wrapper#0@3f071a8
title: Improve issuekit implement progress visibility
---

# Issue #49: Improve issuekit implement progress visibility

## Problem

`issuekit implement <id> --agent <agent> --timeout-sec <n>` runs an external
agent synchronously. The runner redirects the agent stdout/stderr to
`.agent-runs/<run>.out.log` / `.err.log` and then blocks on
`proc.wait(timeout=...)` (see `issuekit/agents/runner.py`). Nothing is printed
to the terminal until the agent exits.

In desktop agent harnesses the agent runs interactively, so progress is visible.
When `issuekit implement` is run from a plain console (for example Ubuntu), the
operator sees an empty terminal for the entire run and cannot tell whether the
agent is working, testing, stuck, or finished. The reporter (py_cr_wrapper) had
to open side terminals for `issuekit runs`, `tail .agent-runs/<run>.err.log`,
and `git status --short` to distinguish active work from a stalled run.

`.agent-runs/` also appears as an untracked path after a run, and the command
output does not explain that it is gitignored run-log storage (already handled
at `init` scaffolding time by #48), which adds to the confusion.

This issue covers proposal points 1, 2, 3, and 6. Point 4 is tracked in #50 and
point 5 in #51.

## Design constraint: do not inflate token usage

`issuekit implement` has two distinct consumers:

- A human watching a real terminal. Live progress is free here.
- An orchestrating agent that captures the command stdout. Every streamed line
  becomes context tokens, so streaming a 30-minute agent log into a captured
  pipe is expensive and unbounded.

The solution must separate "live human view" from "captured result" so token
cost stays flat regardless of run length:

- Stream/heartbeat output is for the human terminal only and must never reach a
  captured pipe.
- The captured stdout (what an agent parses) stays the existing compact final
  summary, plus at most a bounded tail of the log.
- Agents that need a liveness signal poll a small status file instead of
  consuming a stream (constant cost, independent of run duration).

## Proposed Solution

1. TTY-gated live heartbeat (proposal points 1, 2).
   - Add a `--follow` flag to `issuekit implement`. When `--follow` is set, or
     when `sys.stderr.isatty()` is true, emit a heartbeat while the agent runs.
   - Write the heartbeat to `stderr` as an in-place single line using a carriage
     return (`\r`) so it overwrites itself and leaves no scrollback:
     `[mm:ss] running run=<run_id> changed=<n> last: <truncated last log line>`.
   - When `stderr` is not a TTY (output is captured by an agent or piped), emit
     no heartbeat at all. This keeps captured token cost at zero.
   - Heartbeat fields: elapsed time, run id, last log update time, and the most
     recent non-empty agent log line (truncated to a fixed width).
   - Implement the heartbeat with a lightweight watcher (thread or periodic
     poll) while `proc.wait` runs; do not block the agent process.

2. Enrich the run status JSON for cheap agent polling (proposal point 2).
   - The runner already writes `.agent-runs/<run>.status.json` via
     `write_status` (`issuekit/agents/status.py`). Add `last_log_line`,
     `last_log_at`, and a periodically updated `heartbeat_at` while running.
   - An orchestrator checks "alive vs stuck" with a single small JSON read
     (constant token cost), instead of capturing a stream. `issuekit runs`
     should surface the latest line/timestamp from this file.

3. Changed-files visibility (proposal point 3).
   - At completion the command already prints `git --no-pager status --short`.
     Keep that. Additionally, include the changed-file count in the heartbeat
     line so the human can see edits accumulating during the run.
   - Periodic full file listing is optional; the heartbeat `changed=<n>` counter
     is the minimum bar and is cheap to compute.

4. `.agent-runs/` note (proposal point 6).
   - When the run creates `.agent-runs/`, print one short line clarifying it is
     gitignored run-log storage, not issue state, and is not normally committed.
     This mirrors the README guidance and the #48 init scaffolding.

## Impact

- `issuekit/commands/implement.py`: add `--follow` flag (CLI wiring in
  `issuekit/cli.py`), the `.agent-runs/` note, and the changed-file count in
  output. Final captured stdout stays the compact summary.
- `issuekit/agents/runner.py`: non-blocking heartbeat/watcher around
  `proc.wait`, TTY gating, last-log-line tracking, bounded tail for the summary.
- `issuekit/agents/status.py`: extend `RunStatus` with `last_log_line`,
  `last_log_at`, `heartbeat_at`.
- `issuekit/commands/runs.py`: surface the latest log line/timestamp.
- Token behavior: no streaming reaches a non-TTY (captured) consumer; captured
  output remains bounded.

## Implementation Plan

1. Add `last_log_line` / `last_log_at` / `heartbeat_at` to `RunStatus` and
   `write_status`, keeping backward-compatible reads for older status files.
2. In `AgentRunner.run`, start a non-blocking watcher that tails the agent log,
   updates the status JSON, and (only when `stderr.isatty()` or `--follow`)
   renders the in-place `\r` heartbeat line to stderr. Stop the watcher on exit.
3. Add the `--follow` flag to the `implement` subparser and thread it through to
   the runner.
4. Add the `.agent-runs/` note and the `changed=<n>` count to `implement`
   output. Keep the final captured summary compact (existing fields plus an
   optional bounded log tail).
5. Update `issuekit runs` to show the latest log line/timestamp from the status
   JSON.

## Test Plan

- `uv run pytest tests/test_agents_runner.py tests/test_implement_command.py tests/test_runs_command.py`
- Unit test: heartbeat is suppressed when stderr is not a TTY (no streamed
  output captured), and emitted when `--follow` is set or stderr is a TTY.
- Unit test: status JSON gains `last_log_line` / `last_log_at` during a run.
- Manual: run `issuekit implement <id> --agent <agent> --follow` from a console
  and confirm a live single-line heartbeat without scrollback spam.
- `issuekit validate`

## Related Resources

- Origin: `py_cr_wrapper#0@3f071a8`
- Split siblings: #50 (info status+stage), #51 (rename err.log)
- Prior art: #37, #39 (headless runner), #41 (run status), #48 (implement
  hardening, `.agent-runs/` gitignore)
