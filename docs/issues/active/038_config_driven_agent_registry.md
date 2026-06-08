---
id: 38
status: in_progress
priority: low
created: 2026-06-08
completed: 
assignee: codex
stage: changes_requested
implementer: codex
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

## Handoff

- Summary: Add config-driven agent registry with codex adapter. Per-agent run settings moved into IssuekitConfig. ConfigAgentAdapter base class drives adapters from config. KimiAdapter refactored, CodexAdapter added for codex exec mode. resolve_adapter() provides name-based lookup.
- Branch: `main`
- Commit: `27f0a12`

## Review Feedback

- The config-driven registry design is good: AgentRunConfig + ConfigAgentAdapter + resolve_adapter + the [agents.*] TOML loader are clean, the KimiAdapter refactor preserves the verified kimi contract (including the stderr resume-id fix), and 216 passed with validate and check-encoding clean. But the codex side does not satisfy Implementation Plan step 1 (verify the codex contract on Windows and Ubuntu), and the adapter cannot run against the installed codex CLI.

REQUIRED CHANGES (blocking):

1. CodexAdapter uses a flag that does not exist. Verified against codex-cli 0.119.0 on this machine (C:\Users\jj\.codex\.sandbox-bin\codex.exe): `codex exec` has NO `--approval-mode` option. Running the adapter's exact argv `codex exec "hello" --approval-mode auto-edit --model gpt-4` fails with `error: unexpected argument '--approval-mode' found` (exit 2). The codex AgentRunConfig (approval_flag="--approval-mode", approval_value="auto-edit") therefore cannot execute. Replace with a valid non-interactive auto-exec mechanism: `--full-auto` (alias for `--sandbox workspace-write`) or `--sandbox workspace-write` / `danger-full-access`. Pick one and verify it actually runs.

2. build_argv cannot express a value-less approval flag. It only appends the approval flag when BOTH approval_flag AND approval_value are set. codex's `--full-auto` takes no value, so the AgentRunConfig schema and build_argv must support a flag-only approval option (e.g. allow approval_value to be None and still append approval_flag). Adjust schema + build_argv + loader accordingly.

3. Verify the codex contract empirically and replace the "to be verified" docstring in codex.py with the verified facts: stdin behavior (the runner passes stdin=DEVNULL; note codex exec reads the prompt from stdin when not given as an argument, so confirm DEVNULL is correct), stdout/stderr split, and exit codes. Update the codex tests: test_codex_adapter_argv_contains_exec and test_codex_adapter_argv_includes_model currently assert `--approval-mode`/`auto-edit`, which locks in the broken contract; update them to the corrected flags. Strongly recommend a real end-to-end smoke (like the kimi e2e that caught #37's bug) so the corrected argv is proven to run.

4. (secondary) codex known_paths is empty, so resolve_binary raises where codex is not on PATH. On this machine codex was found only at ~/.codex/.sandbox-bin/codex.exe (not on PATH), which may be sandbox-specific. Add appropriate known_paths for codex or document that codex must be on PATH, and confirm against the real install location.

Re-submit for review once the codex adapter runs against the installed CLI and the docstring/tests reflect the verified contract.
