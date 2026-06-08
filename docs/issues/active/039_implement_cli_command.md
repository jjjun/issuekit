---
id: 39
status: active
priority: low
created: 2026-06-08
title: issuekit implement CLI command driving an agent from an issue
---

# Issue #39: issuekit implement CLI command driving an agent from an issue

## Problem

The runner (#37) and registry (#38) have no user-facing entry point. Because an
issuekit issue body is already a self-contained spec, it can serve directly as
the plan for synchronous agent driving, but there is no command to wire the two
together.

## Proposed Solution

Add an `issuekit implement <issue-id> --agent <name>` command that uses the
issue body as the plan, runs the selected agent via the registry, and surfaces
the resulting diff for review. The command never commits; the human or reviewer
commits after inspecting the changes.

1. Resolve the issue file by id, extract its body as the plan (write to a temp
   plan file or pass the issue path).
2. Run the selected agent through the #38 registry and #37 runner.
3. Print the run summary and `git status --short`; leave all changes unstaged.

## Impact

- `issuekit/commands/implement.py` (new): the command implementation.
- `issuekit/cli.py`: register the `implement` subcommand.
- `scripts/run-kimi.ps1`: remove once this command reaches parity.
- `tests/`: command resolves an issue, invokes the runner (faked), leaves no
  commit.

## Implementation Plan

1. Add `issuekit/commands/implement.py` and register it in `issuekit/cli.py`.
2. Load the issue by id, derive the plan, call the runner with the chosen agent.
3. Emit a review summary; do not commit or push.
4. Remove `scripts/run-kimi.ps1` and its `.kimi-runs/` ignore entry once parity
   is confirmed.
5. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_implement_command.py`
- Manual: `issuekit implement <id> --agent kimi` implements a small real issue;
  changes appear unstaged for review with no commit made.
- `uv run issuekit validate`

## Related Resources

- Issue #37 (runner) and Issue #38 (registry); depends on both
- `issuekit/cli.py`, `issuekit/commands/`
- Issue #40 (optional review-gate hookup)
