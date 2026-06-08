---
id: 37
status: active
priority: medium
created: 2026-06-08
title: Python headless agent runner with kimi adapter
---

# Issue #37: Python headless agent runner with kimi adapter

## Problem

We want to drive a coding agent synchronously: one agent (currently Claude)
writes a self-contained plan, a worker agent implements it headless, and a human
or reviewer inspects the diff before committing. A working prototype exists at
`scripts/run-kimi.ps1`, but it is Windows-only PowerShell and hard-wired to
kimi. The project will also run on Ubuntu and will drive agents other than kimi
(codex first), so the durable implementation must be cross-platform Python with
a per-agent adapter seam.

The prototype already proved the runtime contract against kimi-code v0.11.0.
That contract must be preserved exactly so the rewrite does not regress:

- kimi's headless mode is `kimi -p "<prompt>" --output-format text`.
- In `-p` mode kimi auto-executes tools (including file writes) and REJECTS
  `--auto` / `-y` ("Cannot combine --prompt with --auto/--yolo"). There is no
  approval gate in headless mode; the diff review is the only safety net.
- kimi writes reasoning narration to STDERR and the final answer to STDOUT, plus
  a `To resume this session: kimi -r <id>` footer.
- The child must get an empty/closed stdin or it can hang waiting for input
  (same failure class as the git-subprocess stdin hang fixed in propose.py).
- The kimi binary is not on PATH; it must be resolved (PATH first, then known
  per-OS locations) without hard-coding a user-specific path.

## Proposed Solution

Add a cross-platform Python agent runner with a pluggable adapter, plus a kimi
adapter, reaching feature parity with `scripts/run-kimi.ps1`. Keep this issue
to the kimi adapter and the runner core; config-driven multi-agent registration
(#38), the user-facing CLI command (#39), and review-gate integration (#40) are
separate follow-ups.

1. Define an `AgentRunner` core that, given an agent adapter and a plan file
   path, runs the agent headless against a target repo and returns a structured
   result (exit code, stdout/stderr log paths, elapsed time).
2. Define an `AgentAdapter` seam (protocol/ABC) with: `resolve_binary()`,
   `build_argv(prompt, plan_path)`, and `parse_output(stdout, stderr)`.
   Implement `KimiAdapter` encoding the verified kimi contract above.
3. Build the implementation prompt by passing the plan by PATH, never inlining
   the plan body (avoids command-line length limits and quoting issues). The
   prompt instructs the agent to edit files directly and NOT to commit or push.
4. Run the child with `subprocess` using `stdin=subprocess.DEVNULL`, a hard
   timeout that kills the process group on expiry, and stdout/stderr captured to
   timestamped files under a gitignored run-log directory (`.agent-runs/`).
5. After the run, surface changes for review with `git status --short` (lists
   new/untracked files that `git diff --stat` omits). The runner never commits
   or pushes.

## Impact

- `issuekit/agents/runner.py` (new): `AgentRunner`, result dataclass, subprocess
  orchestration (DEVNULL stdin, timeout+kill, log capture).
- `issuekit/agents/adapters/kimi.py` (new): `KimiAdapter` with the verified
  headless contract; binary resolution via PATH then per-OS known locations.
- `issuekit/agents/__init__.py`, `issuekit/agents/adapters/__init__.py` (new).
- `.gitignore`: add `.agent-runs/` (the prototype's `.kimi-runs/` is already
  ignored; keep or supersede).
- `scripts/run-kimi.ps1`: keep as a Windows reference/fallback until the CLI
  lands in #39, then remove.
- `tests/`: cover runner behavior with a fake adapter (no real agent) and kimi
  argv/parse construction.

## Implementation Plan

1. Create the `issuekit/agents/` package: `runner.py` (core + result type) and
   `adapters/kimi.py` (`KimiAdapter`).
2. Implement `AgentRunner.run(adapter, plan_path, repo, timeout, model=None)`:
   build prompt by path, assemble argv via the adapter, spawn with
   `stdin=subprocess.DEVNULL`, enforce timeout with process-group kill, capture
   stdout/stderr to `.agent-runs/<timestamp>.{out,err}.log`, return the result.
3. Implement `KimiAdapter`: `build_argv` -> `["-p", prompt, "--output-format",
   fmt]` (no `--auto`/`-y`); `resolve_binary` -> PATH, then known locations
   (`~/.kimi-code/bin/kimi(.exe)`); `parse_output` -> final answer from stdout,
   reasoning from stderr, capture the resume session id.
4. Print `git status --short` for the target repo after the run; never commit.
5. Add tests: a `FakeAdapter` that runs a trivial cross-platform command proves
   DEVNULL-stdin no-hang, timeout+kill, and log capture; unit tests assert the
   kimi argv contains `-p` and never `--auto`/`-y`, and that parse splits
   stdout/stderr correctly.
6. Run `uv run pytest`, `uv run issuekit validate`, and
   `uv run issuekit check-encoding`.

## Acceptance Criteria

Carried over verbatim from the verified `scripts/run-kimi.ps1` spike; the Python
runner must satisfy all of these:

- Child process receives empty/closed stdin (`subprocess.DEVNULL`) and does not
  hang on input.
- A hard timeout kills the (process group of the) agent and reports a timeout
  result rather than blocking.
- The agent is instructed not to commit or push; the runner itself never runs
  `git commit`/`git push`.
- Run logs are written under a gitignored directory, not into the working tree.
- The kimi binary is resolved without a hard-coded user-specific path (PATH
  first, then per-OS known locations); a clear error is raised if not found.
- The kimi adapter never passes `--auto` or `-y` in headless `-p` mode.
- Verified to run on both Windows and Ubuntu (manual matrix or CI).

## Test Plan

- `uv run pytest tests/test_agents_runner.py`
- Manual (Windows and Ubuntu): point the runner at a throwaway git repo with a
  one-line plan ("create hello.txt containing X"); confirm the file is created,
  no commit is made, logs land under `.agent-runs/`, and `git status --short`
  lists the new file.
- Manual timeout check: a plan/agent that would block is killed at the timeout.
- `uv run issuekit validate`
- `uv run issuekit check-encoding`

## Related Resources

- `scripts/run-kimi.ps1` (verified prototype; source of the acceptance criteria)
- `issuekit/config.py` (`IssuekitConfig.assignees = codex, claude, kimi`)
- `issuekit/cli.py`, `issuekit/commands/` (where #39 wires the CLI entry)
- Memory note: kimi headless contract (path, `-p`, no `--auto`/`-y`, stderr/
  stdout split, empty-stdin no-hang)
- Follow-ups: #38 (config-driven agent registry + codex adapter), #39 (CLI
  `issuekit implement`), #40 (review-gate integration)
- Prior art: stdin-hang fix in `issuekit/commands/propose.py` (`stdin=DEVNULL`)
