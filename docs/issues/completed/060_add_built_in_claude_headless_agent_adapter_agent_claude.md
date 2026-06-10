---
id: 60
status: completed
priority: medium
created: 2026-06-11
completed: 2026-06-11
stage: done
origin: mine-js-monorepo#0@85b56b03
title: Add built-in Claude headless agent adapter (--agent claude)
---

# Issue #60: Add built-in Claude headless agent adapter (--agent claude)

## Problem

Adopted from an incoming cross-project proposal.

## Proposed Solution

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
- Autonomous tool execution: `--permission-mode acceptEdits` auto-accepts file
  edits while still gating other actions (bash, etc.). This is the chosen
  default (see Reviewer decision below). `--permission-mode bypassPermissions`
  removes every gate but is broader than codex `--full-auto`, which runs under a
  `workspace-write` sandbox; bypassPermissions has no sandbox, so it is left as
  an opt-in override only.
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
           approval_value="acceptEdits",
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
     `["-p", "<prompt+suffix>", "--permission-mode", "acceptEdits",
       "--output-format", "text"]`, and appends `["--model", "<m>"]` when a model
     is supplied.

## Test plan

- `uv run pytest tests/test_agents_registry.py tests/test_agents_runner.py`
- `uv run pytest` (full suite) to catch agents-default assertions elsewhere.
- Manual smoke: `issuekit implement <throwaway-id> --agent claude --follow`;
  confirm a row appears in `issuekit runs --json` with `agent="claude"` and
  `exit_code=0`.

## Notes / decisions

- approval_value: default is `acceptEdits` (reviewer decision). A repo that
  wants full unattended autonomy can override to `bypassPermissions` via an
  `[agents.claude]` block in issuekit.toml, since the loader already reads
  `approval_value` (config.py:170).
- Separation of duties is unchanged: when claude is the implementer, the reviewer
  must be a different session. Repos using `default_reviewer = "auto"` already
  satisfy this through the open review pool.
- Scope is additive: only a new adapter file, one tuple entry, one resolve_adapter
  branch, and tests. No changes to existing kimi/codex behavior.

## Reviewer decision

Proposal verified against the codebase and accepted. One change from the
proposal as written: the default `approval_value` is `acceptEdits`, not
`bypassPermissions`. Rationale: codex `--full-auto` is sandboxed
(`workspace-write`, network off), whereas Claude `bypassPermissions` removes
every permission gate with no sandbox, so it is not an equivalent default for a
file-editing implementer. `acceptEdits` auto-accepts edits while still gating
bash and other actions, which is the closer match to the codex autonomy level.
`bypassPermissions` remains available as an explicit per-repo override.

Implementer: when writing the tests in step 4, assert the argv contains
`acceptEdits` (not `bypassPermissions`).

## Impact

- Adopted proposal content should be reviewed locally.

## Implementation Plan

1. Triage the adopted proposal into local implementation steps.

## Test Plan

- Run the relevant local verification commands.

## Related Resources

- Origin: `mine-js-monorepo#0@85b56b03`

## Handoff

- Summary: Added built-in Claude headless agent adapter so `issuekit implement <id> --agent claude` launches Claude Code in print mode. config.py gains a third default agents entry (claude: -p, --permission-mode acceptEdits, --output-format text, --model, shared mojibake guardrail suffix, mojibake_gate, diff_shape_warn_deletions=40). New issuekit/agents/adapters/claude.py adds ClaudeAdapter(ConfigAgentAdapter) with the verified headless contract documented. runner.py resolve_adapter gains a claude branch before the config fallback. Tests assert resolve_adapter('claude') returns ClaudeAdapter and that build_argv yields [-p, prompt, --permission-mode, acceptEdits, --output-format, text] plus [--model, m] when a model is given (acceptEdits, not bypassPermissions). Full suite: 299 passed, 22 skipped; check-encoding clean; all changed files LF/no-BOM/ASCII.
- Branch: `main`
- Commit: `cb08de6`

**Completed**: 2026-06-11

## Completion Notes

- Approved by claude.
- Verification: `Approved. Implementation (commit cb08de6) matches the issue spec exactly.

Code review:
- config.py: third default agents entry "claude" added with binary=claude, known_paths, headless_argv=("-p",), approval_flag=--permission-mode + approval_value=acceptEdits (reviewer decision honored, not bypassPermissions), --output-format text, --model, shared mojibake guardrail suffix, mojibake_gate=True, diff_shape_warn_deletions=40.
- agents/adapters/claude.py (new): ClaudeAdapter(ConfigAgentAdapter) thin subclass mirroring CodexAdapter, verified headless contract in docstring, super().__init__("claude").
- runner.py: resolve_adapter "claude" branch added before the generic config.agents fallback.
- tests: resolve_adapter("claude") -> ClaudeAdapter; build_argv shape asserts -p, acceptEdits, --output-format text, and appends --model; "bypassPermissions" absent. Both test files extended.

Verification:
- uv run pytest (full suite): 299 passed, 22 skipped.
- Encoding/EOL on all 5 changed files: no-BOM, LF-only, ASCII-only (repo convention satisfied).
- Claude Code CLI flags (-p/--print, --permission-mode {acceptEdits|bypassPermissions}, --output-format text|json|stream-json, --model) confirmed accurate.
- Scope is additive; no changes to kimi/codex behavior.

Note (non-blocking, outside this commit): a stray untracked file ./127 (a pyenv error message from a redirect accident) exists in the working tree; not part of the implementation and safe to delete.`
