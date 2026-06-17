---
id: 63
status: completed
priority: medium
created: 2026-06-17
completed: 2026-06-17
stage: done
author: claude
origin: mine-py#0@54e30f0e
title: Merge per-agent config overrides instead of replacing the entire agent registry
---

# Issue #63: Merge per-agent config overrides instead of replacing the entire agent registry

## Problem

`_load_agents` (`issuekit/config.py`) replaces the built-in agent registry
entirely when any `[tool.issuekit.agents.*]` table is present. Two consequences:

1. To change a single field on the shipped `codex` agent (for example to swap
   `approval_flag="--full-auto"` for `--sandbox danger-full-access` on a host
   where bubblewrap cannot run), the operator must re-specify EVERY codex field
   (`headless_argv`, `model_flag`, `prompt_suffix`, `mojibake_gate`,
   `diff_shape_warn_deletions`, ...). Easy to drift from upstream defaults and
   silently drop guardrails.
2. Worse: specifying `[tool.issuekit.agents.codex]` alone DROPS the other
   shipped agents (`kimi`, `claude`) entirely, because `_load_agents` returns
   only the agents present in the raw table. A one-flag override silently
   removes unrelated agents.

This blocks the use case behind the originating proposal: codex hardcodes
`--full-auto` (sandbox `workspace-write`), which requires bubblewrap. On hosts
without working bwrap but where codex is already externally trusted
(`~/.codex/config.toml` sets `sandbox_mode="danger-full-access"`), the operator
needs to change just the sandbox flag without re-declaring the whole agent or
losing kimi/claude.

## Proposed Solution

Make `[tool.issuekit.agents.<name>]` PATCH the built-in default for that agent
name, field by field, and leave unspecified default agents intact.

- Start from the built-in `IssuekitConfig.agents` as a base mapping.
- For each agent name in the raw table: if a built-in default exists, overlay
  only the explicitly provided fields onto a copy of that default
  (`dataclasses.replace`); if no default exists, build a fresh `AgentRunConfig`
  as today (binary defaults to the name).
- Agents present in the defaults but absent from the raw table are preserved
  unchanged.
- Preserve `_load_raw_config` / pyproject precedence and all existing field
  parsing (`model_prompts`, booleans, optional ints/strs).

Decisions to lock in:

- The overlay must distinguish "key absent" (keep default) from "key present
  with empty/false value" (apply it). Use `cfg.get(key, _SENTINEL)` presence
  checks so an explicit `mojibake_gate = false` or a cleared flag is honored
  rather than ignored. Note the current `_optional_str` maps "" -> None; decide
  and test the intended semantics for clearing a flag (for example
  `approval_value = ""`).
- This changes existing semantics from REPLACE to MERGE. Document it. There is
  no current need to delete a default agent via config; if that is ever needed
  it can be a follow-up (for example an explicit `enabled = false`). Do NOT add
  removal in this issue.

Once merged-override lands, the sandbox use case is solved with a minimal table,
with no separate sandbox knob required:

```toml
[tool.issuekit.agents.codex]
approval_flag = "--sandbox"
approval_value = "danger-full-access"
```

without re-declaring guardrails and without dropping kimi/claude. The issue may
optionally document this recipe.

## Impact

- `issuekit/config.py` (`_load_agents` only; `AgentRunConfig` unchanged)
- Changes config semantics for any repo using `[tool.issuekit.agents.*]` from
  replace to per-field merge.

## Implementation Plan

1. Build a name -> default map from `IssuekitConfig.agents`.
2. Rewrite `_load_agents` to overlay provided fields per agent via
   `dataclasses.replace` over the matching default, falling back to a fresh
   `AgentRunConfig` for unknown names.
3. Preserve unspecified default agents in the returned tuple with a
   deterministic order (defaults first in their existing order, then any
   new agents).
4. Use sentinel-based presence checks so explicit empty/false values override.

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- New tests:
  - `[tool.issuekit.agents.codex]` setting only `approval_flag`/`approval_value`
    keeps codex's `prompt_suffix`, `mojibake_gate=True`,
    `diff_shape_warn_deletions`, `headless_argv`, etc. from the default and
    overrides only the two flags.
  - The same override leaves `kimi` and `claude` agents present and unchanged.
  - A brand-new agent name (no built-in default) is still constructed as today.
  - An explicit `mojibake_gate = false` override is honored (not ignored as
    "absent").
- Confirm pyproject precedence and standalone `issuekit.toml` loading are
  unaffected.

## Related Resources

- Origin: `mine-py#0@54e30f0e`
- `issuekit/config.py` (`_load_agents`, `AgentRunConfig`, `IssuekitConfig.agents`)
- Sibling scope: no-op/blocked-run submit gate (separate issue).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-17

## Completion Notes

- Approved by claude.
- Verification: `Approved. codex implementation of #63 is correct, complete, and well-tested.

Implementation (issuekit/config.py):
- `_load_agents` rewritten from REPLACE to per-field MERGE. Each `[agents.<name>]` table overlays only explicitly-present keys onto the built-in default for that name via `dataclasses.replace`; unknown names fall back to a fresh `AgentRunConfig(binary=name)`.
- Presence is detected with a module-level `_SENTINEL` (`cfg.get(key, _SENTINEL)`), so an explicit `mojibake_gate = false` or a cleared `approval_flag = ""` is honored rather than treated as absent.
- Unspecified default agents are preserved; deterministic order = defaults in their original order, then any new agents appended. `_agent_overrides` maps each key to its existing loader (no behavior drift in parsing).

This resolves both reported footguns: changing one codex flag no longer requires re-declaring every field, and no longer drops kimi/claude. The originating sandbox use case now works minimally:
  [tool.issuekit.agents.codex]
  approval_flag = "--sandbox"
  approval_value = "danger-full-access"
keeping codex's guardrails (prompt_suffix, mojibake_gate=True, diff_shape_warn_deletions=40) intact.

Docs: README documents the patch-by-name merge semantics with the sandbox example.

Tests (tests/test_config.py):
- merge override keeps all other codex defaults AND preserves kimi/claude with order ("kimi","codex","claude").
- `mojibake_gate = false` override is honored.
- `approval_flag = ''` clears the optional flag to None.
- The pre-existing guardrail-fields test was correctly updated to look codex up by name (defaults are now preserved alongside).

Verification:
- Full suite: 316 passed, 22 skipped (uv run python -m pytest).
- check-encoding clean; config.py, test_config.py, README.md are LF/no-BOM.

Scope respected: AgentRunConfig unchanged; no agent-removal mechanism added (deferred as noted). Proposal direction B (sandbox selection) is satisfied via this merge path, so no separate knob was needed.`
