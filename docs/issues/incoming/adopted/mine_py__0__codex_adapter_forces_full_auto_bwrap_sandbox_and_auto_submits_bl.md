---
origin: mine-py#0@54e30f0e
to: issuekit
reply_to: 
created: 2026-06-09
title: Codex adapter forces --full-auto (bwrap sandbox) and auto-submits blocked/empty runs
---

# Proposal: Codex adapter forces --full-auto (bwrap sandbox) and auto-submits blocked/empty runs

# Codex adapter forces `--full-auto` (bwrap sandbox) and auto-submits blocked/empty runs

## Context

Reporting repo: mine-py. Observed with the issuekit uv tool install and OpenAI
Codex CLI v0.137.0 on Ubuntu (Linux 6.8).

While driving `issuekit implement <id> --agent codex`, the run completed with
`exit_code=0` and `submitted_review`, but produced ZERO implementation changes.
Inspecting the agent log showed Codex never ran a single shell command:

```
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
...
warning: Codex could not find bubblewrap on PATH. ... Codex will use the
bundled bubblewrap in the meantime.
I'm blocked by the execution environment before I can read or edit files.
Even trivial commands like `pwd` fail ...
```

Root cause on this host:

- The Codex adapter hardcodes `approval_flag="--full-auto"`
  (`issuekit/agents/adapters/codex.py` contract + `issuekit/config.py` default
  for the `codex` agent), which selects sandbox `workspace-write`.
- `workspace-write` requires bubblewrap. This host has no system `bwrap`, and
  AppArmor restricts unprivileged user namespaces
  (`kernel.apparmor_restrict_unprivileged_userns = 1`), so the bundled `bwrap`
  cannot set up loopback and every command (even `pwd`) fails.
- The operator's own `~/.codex/config.toml` already sets
  `sandbox_mode = "danger-full-access"` and `approval_policy = "never"`
  (Codex is externally trusted on this machine), but the hardcoded
  `--full-auto` CLI flag overrides that config, so issuekit cannot use the
  working configuration.

## Problems

1. No clean way to change the Codex sandbox mode.
   The only override path is a full `[tool.issuekit.agents.codex]` table in the
   consuming repo's `pyproject.toml`. Because `_load_agents` REPLACES the
   default agents entirely when an `agents` table is present (it does not merge
   with `IssuekitConfig.agents`), the operator must re-specify every field
   (`headless_argv`, `model_flag`, `prompt_suffix`, `mojibake_gate`,
   `diff_shape_warn_deletions`, ...) just to change one flag. Easy to drift from
   upstream defaults and easy to silently drop guardrails.

2. A blocked / no-op run is still auto-submitted for review.
   Codex made zero file changes and explicitly reported it was blocked, yet
   `issuekit implement` advanced the issue to `stage=review` with
   `exit_code=0`. The reviewer then sees an empty diff masquerading as a
   completed implementation. The run heartbeat even showed `changed=3` (only
   the issue/index/settings churn), i.e. no implementation files touched.

## Suggested directions (issuekit owns the final design)

A. Per-agent override that MERGES with defaults instead of replacing.
   Let `[tool.issuekit.agents.codex]` patch only the specified fields over the
   built-in default for that agent name, so changing one flag does not require
   copying the whole config. (Or add a dedicated, documented sandbox/approval
   knob for the codex adapter.)

B. Make the Codex sandbox selectable.
   Allow choosing `--sandbox <mode>` (e.g. `danger-full-access`) instead of the
   fixed `--full-auto`, or default to honoring the user's `~/.codex/config.toml`
   when no explicit sandbox is requested. This is needed for hosts where
   bubblewrap cannot run but Codex is already externally sandboxed/trusted.

C. Do not auto-submit a run that produced no implementation changes.
   When the implementer touches zero non-tracker files (or the agent exits
   reporting a hard environment block), keep the issue in implementation with a
   clear failure status instead of advancing it to `review`. Optionally treat a
   known "blocked before any command ran" signal as a non-success outcome even
   when the agent process exits 0.

## Repro

1. Host without working bubblewrap (no system `bwrap`; AppArmor restricts
   unprivileged userns), Codex CLI installed, `~/.codex/config.toml` set to
   `sandbox_mode = "danger-full-access"`.
2. `issuekit implement <id> --agent codex --timeout-sec 800 --follow`.
3. Observe: every Codex shell command fails with the bwrap loopback error;
   no files are edited; issuekit still reports `exit_code=0` and
   `submitted_review stage=review`.

## Workaround currently used downstream

A full `[tool.issuekit.agents.codex]` override in the consuming repo replicating
the defaults but swapping `approval_flag = "--sandbox"` /
`approval_value = "danger-full-access"`. This works but is verbose and brittle
for the reasons in Problem 1, which is why this is filed as a proposal rather
than kept as a local config hack.
