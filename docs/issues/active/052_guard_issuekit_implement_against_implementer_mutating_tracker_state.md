---
id: 52
status: active
priority: high
created: 2026-06-08
completed: 
stage: todo
author: claude
title: Guard issuekit implement against implementer mutating tracker state
---

# Issue #52: Guard issuekit implement against implementer mutating tracker state

## Problem

`issuekit implement <id> --agent <agent>` claims the issue, launches the
external agent to edit code, then calls `submit_for_review`. The agent prompt
(built in `issuekit/agents/runner.py`) tells the agent to implement the plan by
editing files and not to run `git commit`, but nothing stops the agent from
editing the tracker itself under `docs/issues/`.

Observed on 2026-06-08 while implementing issue #49 with the `kimi` agent: the
agent moved `docs/issues/active/049_*.md` into `completed/` and rewrote the
generated indexes, effectively completing the issue on its own. The subsequent
`submit_for_review` step then failed with "Active issue #49 was not found" and
`issuekit implement` exited 1, even though the agent's code work was fine
(agent `exit_code=0`).

This is two problems at once:

1. It breaks the implement run: `submit_for_review` cannot find the issue in
   `active/`, so the orchestrated run reports failure despite good code.
2. It violates separation of duties: the implementer self-completed (and thus
   self-reviewed) the issue, which the tracker is designed to reject. Recovery
   required hand-restoring `active/049` and the indexes and deleting the stray
   `completed/049` before a separate reviewer could approve.

## Proposed Solution

Layer the defenses; the prompt clause is the minimum bar and the runner guard is
the real fix.

1. Strengthen the implement prompt in `runner.py`: explicitly instruct the agent
   to edit only code and tests and to never touch `docs/issues/` (no moving,
   creating, deleting, or editing issue files or indexes). The tracker lifecycle
   is owned by issuekit, not the implementer.
2. Snapshot-and-guard in the runner: before launching the agent, record the
   target issue's tracker path and a cheap clean-state snapshot of
   `docs/issues/`. After the agent exits, detect whether the implementer mutated
   `docs/issues/` (issue moved out of `active/`, a `completed/<id>` appeared,
   indexes changed). If so, restore that subtree (git checkout of tracked issue
   files plus removal of stray untracked issue files) and emit a warning that the
   implementer touched the tracker and it was reset. Reverting-and-continuing is
   the better default because the code work is usually fine.
3. Make `submit_for_review` robust: if the issue is no longer in `active/`
   because the implementer moved it, detect that specific condition and emit a
   targeted error (and ideally trigger the revert from step 2) instead of the
   generic "Active issue not found".

## Impact

- `issuekit/agents/runner.py`: implement prompt text; optional pre/post tracker
  snapshot and guard.
- `issuekit/commands/implement.py`: handle the tracker-mutation case around
  `submit_for_review` and surface a clear message.
- Possibly a shared helper to detect and revert tracker mutations under
  `docs/issues/`.
- `docs/issues/README.md`: document that implementers must not touch tracker
  state.

## Implementation Plan

1. Add an explicit "do not modify docs/issues/ or indexes" clause to the
   implement prompt in `runner.py`.
2. Capture the issue file path and a cheap snapshot of `docs/issues/` cleanliness
   before launch.
3. After the agent exits, detect tracker mutations under `docs/issues/`. If
   found, restore that subtree (git checkout of tracked files plus removal of
   stray untracked issue files) and log a warning.
4. Ensure `submit_for_review` runs against the restored active issue; if
   restoration is not possible, fail with a specific, actionable message.
5. Add tests for both the revert-and-continue path and the prompt clause.

## Test Plan

- Unit/integration: simulate an agent that moves the active issue to `completed/`
  and edits indexes; assert the runner restores the tracker and
  `submit_for_review` succeeds (or fails with the targeted message under a
  fail-fast mode).
- Unit: the built implement prompt contains the do-not-touch-tracker clause.
- `uv run pytest tests/test_agents_runner.py tests/test_implement_command.py`
- `issuekit validate`

## Related Resources

- Observed while implementing #49 (live heartbeat) with kimi on 2026-06-08;
  recovery required a manual tracker restore.
- `issuekit/agents/runner.py` (AgentRunner.run prompt and post-run handling)
- `issuekit/commands/implement.py` (submit_for_review path)
- Separation-of-duties invariants in `get_protocol` and `docs/issues/README.md`
- Prior art: #48 (implement hardening), #49-#51 (progress visibility)
