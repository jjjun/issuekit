---
id: 59
status: completed
priority: high
created: 2026-06-09
completed: 2026-06-09
stage: done
origin: mine-js-monorepo#0@3cd65ca0
title: Enforce codex guardrails: mojibake gate + minimal-additive diffs when driving GPT-5.3-Codex-Spark
---

# Issue #59: Enforce codex guardrails: mojibake gate + minimal-additive diffs when driving GPT-5.3-Codex-Spark

## Problem

Adopted from an incoming cross-project proposal.

## Proposed Solution

# Proposal: Enforce codex guardrails: mojibake gate + minimal-additive diffs when driving GPT-5.3-Codex-Spark

# Proposal: enforce codex guardrails (mojibake gate + minimal-additive diffs) when driving GPT-5.3-Codex-Spark

## Context

This proposal comes from a real session in `mine-js-monorepo` where issuekit drove
the configured `codex` agent (GPT-5.3-Codex-Spark 5) through issues #513, #514, and
#515 via `issuekit implement <id> --agent codex`. The functional output was good
(tests passed, TypeScript/ESLint clean), but the agent exhibited two repeatable,
high-cost failure modes that the reviewer had to catch by hand and that issuekit is
well positioned to prevent at the point where it launches the agent.

The goal of this proposal: when issuekit drives codex, inject guardrail
instructions and/or a pre-submit gate so these behaviors are blocked or
auto-detected before `submit_for_review`, rather than relying on the human/AI
reviewer to notice them every time.

## Observed failure mode 1: mojibake on existing non-ASCII content (hard to detect)

When codex edited a file that already contained non-ASCII text (Japanese doc
comments in `apps/mine-dashboard/lib/aiClient.ts`), it re-encoded the existing
multibyte characters into garbage bytes (mojibake). Example: an existing comment
written in Japanese (a "503/429 retry" note) was turned into a run of garbled
CJK-looking bytes after the edit, while the surrounding ASCII code was unchanged.

Why this is dangerous:
- It silently corrupts pre-existing, untouched content.
- The repo's encoding gate runs `issuekit check-encoding --no-mojibake`, i.e. with
  mojibake detection DISABLED, so the existing CI gate does not catch it. Only a
  byte-level / manual read caught it.
- It survived one round of `request_changes`: when asked to fix it, codex replaced
  the corrupted Japanese with rewritten English rather than restoring the original,
  and still left the rest of the file churned.

## Observed failure mode 2: wholesale reformatting / scope creep instead of additive diffs

The issue explicitly said: "Add `callAiChatMessages` ALONGSIDE `callAiChat`; do not
change `callAiChat`." Codex instead reformatted the entire file: flipped every
single-quote to double-quote, deleted JSDoc blocks, translated/removed existing
comments, reworded error strings, and added trailing commas on untouched lines. The
diff for what should have been a purely additive change was +123/-42.

This violated the issue's own "no unnecessary changes / no reformatting of lines the
change does not need" acceptance gate. It also did not self-correct: after an
explicit `request_changes` saying "reset the file to HEAD and re-apply ONLY the new
functions in the existing style," codex submitted a second time still fully
reformatted (+124/-43). The reviewer ultimately had to reset the file to HEAD and
re-apply the additive code directly to land it (final diff: +108/-0).

## Suggested enforcement (issuekit side)

When `issuekit implement --agent codex` (or the configured codex agent profile)
launches the agent, consider any of:

1. Mojibake gate on by default for codex runs. Before accepting/submitting, run an
   encoding check WITH mojibake detection (i.e. do not pass `--no-mojibake`, or run
   a dedicated mojibake pass) over the agent's touched files, and block
   `submit_for_review` if it trips. At minimum, surface a warning in the run output.

2. Diff-shape self-check. After the agent finishes, compute the per-file diff vs the
   base ref and flag files where deletions on pre-existing lines are large relative
   to the net additive intent (e.g. a file the issue said to "add to" comes back
   with heavy `-` churn). This catches the "reformatted the whole file" pattern.

3. Standing instruction injected into the codex system/launch prompt, e.g.:
   - "Make minimal, additive diffs. Do not reformat, re-quote, re-order imports, or
     rewrite/translate comments on lines unrelated to your change."
   - "Never alter existing non-ASCII (e.g. Japanese) text. Preserve existing
     comments byte-for-byte unless the task is specifically to change them. After
     editing, verify you introduced no mojibake."
   - "When a task says 'add X alongside Y, do not change Y,' the diff must touch only
     the added region; if you cannot, stop and report instead of reformatting."

4. Optional: on `request_changes`, pass the prior reviewer notes back into the codex
   prompt verbatim and add "address ONLY these notes; do not re-touch unrelated
   lines," since the agent twice ignored a scoped change request.

## Why this belongs in issuekit

issuekit owns the launch path for the codex agent and already owns the
`check-encoding` tooling. Centralizing these guardrails there means every repo that
delegates to GPT-5.3-Codex-Spark via issuekit benefits, without each repo having to
re-encode the same CLAUDE.md warnings and without depending on a human/AI reviewer
to catch the same two issues on every run.

## Evidence pointers (in mine-js-monorepo)

- Failure mode 1 & 2: `apps/mine-dashboard/lib/aiClient.ts` across issue #514's two
  codex submissions (mojibake on lines that were originally Japanese; +123/-42 then
  +124/-43 reformatting churn). Resolved by reviewer reset-to-HEAD + additive
  re-apply (+108/-0).
- The repo already documents the BOM/CRLF half of this in CLAUDE.md ("codex keeps
  adding UTF-8 BOM/CRLF") and gates BOM via `check:errors`; mojibake is the gap.

## Design decisions (locked by author)

This issue adopts ALL four enforcement ideas from the proposal (#1 mojibake gate,
#2 diff-shape check, #3 prompt guardrails, #4 request_changes note re-injection),
plus a fifth fix that the proposal exposed but did not name:

5. Default-model pinning per agent. Root cause of the recent "GPT-5.3-Codex-Spark
   5" vs "GPT-5.5" drift: issuekit never pins a model. `AgentRunConfig` for codex
   only carries `model_flag="--model"` with no value, and `runner.build_argv`
   appends `--model` only when the CLI `--model` flag is set. With no flag,
   issuekit passes nothing and codex falls back to whatever the codex CLI default
   resolves to at the time. Pinning a default model in config makes the launched
   model deterministic regardless of CLI-default drift.

Per-model instruction branching uses EXACT model-name keys (no prefix/family
matching). The model used for the lookup is the RESOLVED model:
`resolved_model = cli_model or run_config.model`.

## Implementation Plan

### A. Config surface (`issuekit/config.py`)

Extend `AgentRunConfig` with three new optional fields (all backward compatible):

- `model: str | None = None` -- default model name. Used as the value for
  `model_flag` when the CLI `--model` flag is absent.
- `prompt_suffix: str | None = None` -- agent-wide extra instructions appended to
  the base implement prompt.
- `model_prompts: tuple[tuple[str, str], ...] = ()` -- exact model-name keyed
  extra instructions (parsed from a TOML table/dict), appended after
  `prompt_suffix` only when `resolved_model` matches a key exactly.

Add three new per-agent gate knobs (default OFF except where noted for codex):

- `mojibake_gate: bool` -- run a mojibake pass over the agent's touched files
  before `submit_for_review`; block on a hit. Default TRUE for the shipped codex
  profile, FALSE otherwise.
- `diff_shape_warn_deletions: int | None = None` -- if set, warn (do not block)
  for any touched file whose deleted-line count vs the base ref exceeds this.
  Shipped codex default: a conservative value (e.g. 40).

Update `_load_agents` to parse the new keys, including a nested dict for
`model_prompts`. Keep `_load_raw_config` / pyproject precedence unchanged.

### B. Shipped codex defaults (`issuekit/config.py`)

Set the built-in codex `AgentRunConfig` so every repo benefits without local
config. Add `prompt_suffix` containing the three guardrail instructions from
proposal #3 (minimal additive diffs; never alter existing non-ASCII / verify no
mojibake; "add X alongside Y" must touch only the added region or stop and
report). Set `mojibake_gate=True` and a `diff_shape_warn_deletions` default.
Do NOT hardcode a default `model` value in shipped code (model names churn);
model pinning is a per-repo config knob, documented below.

### C. Prompt assembly (`issuekit/agents/runner.py` + adapters)

The base implement prompt stays in `AgentRunner.run`. Move suffix assembly into
`ConfigAgentAdapter.build_argv` (so both kimi and codex inherit it via config):
append `run_config.prompt_suffix`, then the `model_prompts` entry matching
`resolved_model` (exact), to the prompt before it is placed in argv. Use
`resolved_model = self.model or self.run_config.model`; use that same value for
the `--model` argv so pinning and branching agree.

### D. Mojibake gate (`issuekit/commands/implement.py`)

After the agent run and BEFORE `submit_for_review`, when the resolved agent's
`mojibake_gate` is on: enumerate touched files from the working tree (e.g.
`git status --short` / `git diff --name-only` against HEAD), skip anything under
`docs/issues/` (restored separately) and deleted paths, read each as bytes/utf-8,
and run `issuekit.core.has_mojibake`. On any hit: do NOT submit, print the
offending files, and return non-zero. Reuse the existing `MOJIBAKE_PATTERN` /
`has_mojibake` (core.py) -- it matches only known corruption codepoints, so the
false-positive risk on legitimate CJK is low.

### E. Diff-shape warning (`issuekit/commands/implement.py`)

When `diff_shape_warn_deletions` is set, compute per-file deleted-line counts via
`git diff --numstat` (vs HEAD), and print a WARNING for files over the threshold.
This is advisory only (never blocks); it catches the "reformatted the whole file"
pattern without needing to know additive intent.

### F. request_changes note re-injection (#4)

When `issuekit implement <id>` runs an issue currently in the
`changes_requested` stage, locate the prior reviewer notes on the issue and
append them verbatim to the prompt, prefixed with: "A reviewer requested the
following changes. Address ONLY these notes; do not re-touch unrelated lines:".
Find where review feedback is persisted (issue body review section / workflow
record) before wiring this; if notes cannot be located, skip silently rather
than fabricate.

## Test Plan

- `uv run python -m pytest` (full suite) must pass; run with `uv run python`
  (bare `python`/`python3` fail in this env).
- New unit tests:
  - config: new fields parse from `[tool.issuekit.agents.codex]` incl. a
    `model_prompts` table and a pinned `model`; defaults preserved when absent.
  - build_argv: `resolved_model = cli_model or config.model`; `--model` uses it;
    prompt gets `prompt_suffix` then exact-match `model_prompts` entry; no
    branch when no exact key matches.
  - mojibake gate: a touched file containing a `MOJIBAKE_PATTERN` codepoint
    blocks submit (non-zero, no `submit_for_review`); a clean tree submits;
    `docs/issues/` paths are ignored.
  - diff-shape: a file over the deletion threshold prints a warning but still
    submits.
- Verify shipped codex defaults: with no local config, codex runs carry the
  guardrail `prompt_suffix` and `mojibake_gate=True`.
- Manual: confirm `issuekit implement <id> --agent codex --model gpt-5.5` pins
  the model and that omitting `--model` uses the configured default model when
  one is set in the target repo's config.

## Acceptance criteria

- Driving codex with no `--model` is deterministic when a default `model` is
  configured; the CLI `--model` flag still overrides it.
- Per-model guardrails branch on exact resolved-model name.
- A codex run that introduces mojibake into a touched file is blocked before
  review by default.
- Heavy-deletion files are surfaced as warnings.
- No behavior change for kimi unless its config opts in; all existing tests pass;
  files written UTF-8 (no BOM), LF endings; `docs/issues/` stays ASCII-only.

## Related Resources

- Origin: `mine-js-monorepo#0@3cd65ca0`
- Launch path: `issuekit/commands/implement.py`, `issuekit/agents/runner.py`
- Config: `issuekit/config.py` (`AgentRunConfig`)
- Adapters: `issuekit/agents/adapters/codex.py`, `.../kimi.py`
- Mojibake detection: `issuekit/core.py` (`MOJIBAKE_PATTERN`, `has_mojibake`)

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-09

## Completion Notes

- Approved by claude.
- Verification: `Reviewed codex implementation of #59. All 5 scoped items present and correct: (A/B) AgentRunConfig gains model/prompt_suffix/model_prompts/mojibake_gate/diff_shape_warn_deletions with shipped codex defaults (guardrail prompt_suffix, mojibake_gate=True, threshold=40). (C) build_argv resolves model = cli_model or config.model and uses it for both --model and exact-match model_prompts lookup. (D) mojibake gate runs has_mojibake over git-touched files (excluding docs/issues), blocks submit on hit. (E) diff-shape emits non-blocking heavy-deletion warnings via git diff --numstat. (F) review-feedback re-injection reads the '## Review Feedback' block (matches workflow._review_feedback_note) for changes_requested runs. Diff is +553/-5 (fully additive). Verified: no CRLF/BOM on any modified .py; full suite `uv run python -m pytest` = 292 passed, 22 skipped; new tests cover model resolution, exact-match branching, mojibake block, tracker-path exclusion, non-blocking diff warning, and #4 re-injection. Removed a stray repo-root artifact file '127' (an accidental pyenv-error redirect from the codex run, unrelated to the change).`
