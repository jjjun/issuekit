---
id: 48
status: active
priority: medium
created: 2026-06-08
completed: 
stage: todo
author: claude
title: Harden issuekit implement runs: uncommitted warning, agent-runs gitignore, log decode
---

# Issue #48: Harden issuekit implement runs: uncommitted warning, agent-runs gitignore, log decode

## Problem

Three robustness gaps surfaced when infra-toolkit ran `issuekit implement`
against a real external agent (their issue #120):

1. A successful implement run leaves changes unstaged (by design: diff review is
   the safety net, see #37/#39) and moves the issue to review, but prints no
   explicit warning. issuekit state can therefore read "in review" while Git is
   still uncommitted.
2. `.agent-runs/` is created as an untracked working-tree artifact in consuming
   repos because `issuekit init` only scaffolds `issuekit.local.toml` into
   `.gitignore`, not `.agent-runs/`.
3. Agent run stdout logs can contain mojibake / undecodable bytes that the
   external agent emits. The runner reads logs with
   `read_text(encoding="utf-8")` and no error handling, which can raise on bad
   bytes.

Origin: infra-toolkit#0@10762a8.

## Proposed Solution

1. After a successful `issuekit implement` run with uncommitted changes, print
   an explicit warning (the command already prints `git status --short`)
   clarifying that changes are unstaged and not yet committed, and that
   reviewing and committing is the operator's next step. Optionally record a
   commit-missing flag in the run status JSON.
2. Add `.agent-runs/` to the `.gitignore` entries scaffolded by
   `issuekit init`, alongside `issuekit.local.toml`.
3. Read and parse agent run logs defensively (for example `errors="replace"`)
   so undecodable agent output does not crash the runner or review tooling.
   issuekit already writes logs as UTF-8; only the read-back of agent-emitted
   bytes needs hardening. Document that external-agent mojibake is the agent's
   own output, not issuekit corruption.

## Impact

- `issuekit/commands/implement.py`: explicit uncommitted-changes warning on a
  successful run.
- `issuekit/commands/init.py`: add `.agent-runs/` to the scaffolded `.gitignore`
  entries.
- `issuekit/agents/runner.py`: robust log read (errors handling); optional
  status JSON commit-missing field.
- `tests/`: warning emitted when uncommitted; init writes `.agent-runs/`; runner
  tolerates undecodable bytes.

## Implementation Plan

1. Add the post-run uncommitted warning in `implement.py` (only when there are
   changes and no implementation commit is recorded).
2. Extend the init `.gitignore` scaffold to include `.agent-runs/`.
3. Harden log reads in `runner.py` with `errors="replace"` while keeping writes
   UTF-8.
4. Add tests for each, then run `uv run pytest`, `uv run issuekit validate`,
   `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_implement_command.py tests/test_init.py`
  (and the runner tests as applicable)
- Manual: an implement run with changes prints the uncommitted warning;
  `issuekit init` adds `.agent-runs/`; a log with invalid bytes is read without
  raising.
- `uv run issuekit validate`

## Related Resources

- Origin proposal: infra-toolkit#0@10762a8
- `issuekit/commands/implement.py`, `issuekit/commands/init.py`,
  `issuekit/agents/runner.py`
- Issue #37/#39 (implement never commits by design; this only adds a warning)
- Sibling adoptions: the protocol/docs issue and the CLI `approve` alias issue
