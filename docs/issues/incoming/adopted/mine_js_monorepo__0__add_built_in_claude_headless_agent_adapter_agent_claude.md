---
origin: mine-js-monorepo#0@85b56b03
to: issuekit
reply_to: 
created: 2026-06-11
title: Add built-in Claude headless agent adapter (--agent claude)
---

# Proposal: Add built-in Claude headless agent adapter (--agent claude)

## Problem

`issuekit implement <id> --agent claude` fails with `Unknown agent: claude`.

The `claude` name is already a first-class participant elsewhere in the config:
`IssuekitConfig.assignees = ("codex", "claude", "kimi")` and the default
`default_reviewer` is `"claude"` (issuekit/config.py:36, :38). But there is no
runnable adapter for it:

- `resolve_adapter` only branches on `kimi` and `codex`, then falls back to
  `config.agents` (issuekit/agents/runner.py:104-121).
- The default `IssuekitConfig.agents` tuple defines only `kimi` and `codex`
  (issuekit/config.py:41-79).

Net effect: Claude can author and review, but cannot be driven as a headless
implementer the way codex/kimi can. A consuming repo that wants
"claude implements -> a different claude session reviews -> complete" (allowed
under separation-of-duties when `default_reviewer = "auto"`, since same-name
review is permitted through the open review pool) has no way to launch the
implementer step.

Goal: make `claude` a built-in agent so `issuekit implement <id> --agent claude`
launches Claude Code in headless mode, mirroring the codex/kimi adapters. No
AgentRunConfig schema change is required.

## Claude Code headless contract

Flags for the Claude Code CLI (`claude`):

- Non-interactive print mode: `claude -p "<prompt>"` (alias `--print`). The
  prompt is read from argv, so `stdin=subprocess.DEVNULL` is safe (same as the
  codex contract).
- Autonomous tool execution: `--permission-mode bypassPermissions` skips all
  permission prompts. This is the closest analog to codex `--full-auto`. Safer
  alternative for edit-only autonomy: `--permission-mode acceptEdits`.
- Output format: `--output-format text` (also `json`, `stream-json`).
- Model selection: `--model <name>`.
- Final answer goes to stdout; session/diagnostic logs go to stderr; exit code 0
  on success, non-zero on failure.

This maps cleanly onto the existing AgentRunConfig fields, including
`approval_value` (config.py:20), which build_argv already emits as
`approval_flag approval_value` (runner.py:74-77).

## Implementation plan

1. issuekit/config.py - add a third entry to the default `IssuekitConfig.agents`
   tuple, after the codex entry (around line 78):

   ```python
   (
       "claude",
       AgentRunConfig(
           binary="claude",
           known_paths=(
               "~/.claude/local/claude",
               "~/.claude/local/claude.exe",
               "~/.local/bin/claude",
               "~/.local/bin/claude.exe",
           ),
           headless_argv=("-p",),
           approval_flag="--permission-mode",
           approval_value="bypassPermissions",
           output_format_flag="--output-format",
           output_format="text",
           model_flag="--model",
           prompt_suffix=(
               "Make minimal, additive diffs. Do not reformat, re-quote, "
               "re-order imports, or rewrite/translate comments on lines "
               "unrelated to your change.\n"
               "Never alter existing non-ASCII (e.g. Japanese) text. Preserve "
               "existing comments byte-for-byte unless the task is specifically "
               "to change them. After editing, verify you introduced no mojibake.\n"
               "When a task says 'add X alongside Y, do not change Y,' the diff "
               "must touch only the added region; if you cannot, stop and report "
               "instead of reformatting."
           ),
           mojibake_gate=True,
           diff_shape_warn_deletions=40,
       ),
   ),
   ```

   `known_paths` are best-effort; `shutil.which("claude")` is the primary
   resolution path (runner.py:56-67), so PATH installs work without listing them.

2. issuekit/agents/adapters/claude.py (new) - add `ClaudeAdapter(ConfigAgentAdapter)`
   mirroring CodexAdapter/KimiAdapter; `super().__init__("claude", config=config,
   model=model)`. Put the verified headless contract in the docstring. The default
   `parse_output` is sufficient (override only if a resume-session id is wanted,
   the way KimiAdapter parses one).

3. issuekit/agents/runner.py - add a `claude` branch to `resolve_adapter` before
   the generic `config.agents` fallback (around line 115):

   ```python
   if agent_name == "claude":
       from issuekit.agents.adapters.claude import ClaudeAdapter

       return ClaudeAdapter(config=config, model=model)
   ```

4. Tests - extend tests/test_agents_registry.py and tests/test_agents_runner.py:
   - `resolve_adapter("claude")` returns a `ClaudeAdapter`.
   - `build_argv(prompt, plan)` yields
     `["-p", "<prompt+suffix>", "--permission-mode", "bypassPermissions",
       "--output-format", "text"]`, and appends `["--model", "<m>"]` when a model
     is supplied.

## Test plan

- `uv run pytest tests/test_agents_registry.py tests/test_agents_runner.py`
- `uv run pytest` (full suite) to catch agents-default assertions elsewhere.
- Manual smoke: `issuekit implement <throwaway-id> --agent claude --follow`;
  confirm a row appears in `issuekit runs --json` with `agent="claude"` and
  `exit_code=0`.

## Notes / decisions

- approval_value: recommend `bypassPermissions` to match codex `--full-auto`
  autonomy. It is overridable per consuming repo via an `[agents.claude]` block
  in issuekit.toml, since the loader already reads `approval_value`
  (config.py:170). If a more conservative default is preferred, use
  `acceptEdits`.
- Separation of duties is unchanged: when claude is the implementer, the reviewer
  must be a different session. Repos using `default_reviewer = "auto"` already
  satisfy this through the open review pool.
- Scope is additive: only a new adapter file, one tuple entry, one resolve_adapter
  branch, and tests. No changes to existing kimi/codex behavior.
