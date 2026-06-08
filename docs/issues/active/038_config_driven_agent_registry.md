---
id: 38
status: active
priority: low
created: 2026-06-08
title: Config-driven agent registry with codex adapter
---

# Issue #38: Config-driven agent registry with codex adapter

## Problem

The agent runner from #37 hard-codes its kimi adapter. To drive other agents
(codex first) without editing code for each one, agent definitions should be
declarative configuration, and a second concrete adapter should prove the
abstraction generalizes beyond kimi.

## Proposed Solution

Move per-agent run settings into issuekit configuration and add a codex adapter.

1. Extend issuekit config (see `issuekit/config.py`, `IssuekitConfig`) with a
   per-agent run table: binary name/known paths, headless flag(s), approval
   semantics (kimi headless takes none), and output-parsing hints.
2. Resolve an adapter by agent name from config so registered agents
   (`codex`, `claude`, `kimi`) can be selected without code changes.
3. Add a `CodexAdapter` using codex's non-interactive mode (`codex exec`),
   capturing its own headless contract (to be verified, mirroring how #37
   verified kimi).

## Impact

- `issuekit/config.py`: add the per-agent run config schema and loading.
- `issuekit/agents/adapters/codex.py` (new): `CodexAdapter`.
- `issuekit/agents/runner.py`: resolve adapter by name from config.
- `tests/`: config parsing and adapter selection; codex argv construction.

## Implementation Plan

1. Verify codex's headless contract on Windows and Ubuntu (analogous to the #37
   kimi spike): non-interactive flag, stdin behavior, exit codes, output split.
2. Define the config schema and load it; default the three known agents.
3. Implement `CodexAdapter` and name-based adapter resolution in the runner.
4. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_agents_registry.py`
- Manual: select `--agent codex` and `--agent kimi` against a throwaway repo;
  both implement a one-line plan with no commit.
- `uv run issuekit validate`

## Related Resources

- Issue #37 (runner core and adapter seam; depends on it)
- `issuekit/config.py`
- Issue #39 (CLI entry consumes the registry)
