---
origin: infra-toolkit#0@7522a33
to: issuekit
reply_to: 
created: 2026-06-05
title: Add read-only issuekit setup check for orchestrators
---

# Proposal: Add read-only issuekit setup check for orchestrators

## Problem

infra-toolkit needs to fan out issuekit setup checks across local and remote repositories, but the current stable command, `issuekit setup --json`, applies the repo scaffold before reporting diagnostics. That is useful for onboarding, but it is too strong for routine orchestration because a read/check pass can still touch repo files such as generated indexes or missing MCP scaffold files.

This leaves infra-toolkit with an awkward choice: either always run the applying setup command across every configured repo, or reimplement issuekit-owned scaffold checks in infra-toolkit. The latter would duplicate issuekit policy and drift from the source of truth.

## Proposal

Add an issuekit-owned read-only setup check contract, and clarify the apply command boundary.

Suggested shape:

- Add `issuekit setup check --json` or `issuekit setup --check --json`.
- The check command must not write files and must not run subprocesses.
- It should report whether repo scaffold is current, missing, stale, or blocked.
- Keep the existing applying behavior available as `issuekit setup --json`, or add an explicit `issuekit setup apply --json` alias.
- Include machine-readable fields such as `ok`, `needs_setup`, `would_write`, `would_update`, `diagnostics`, and `actions`.
- Keep scaffold ownership inside issuekit. Consumers should not inspect `.mcp.json`, `.codex/config.toml`, AGENTS.md, CLAUDE.md, or index templates directly.

## Intended Consumer

infra-toolkit would use the read-only check while looping over configured repos and hosts. It would call the apply command only for repos where issuekit says setup is needed, and would aggregate the JSON output without knowing scaffold internals.

## Benefits

- Keeps setup/scaffold policy in issuekit.
- Lets infra-toolkit remain a host/repo orchestrator only.
- Avoids unnecessary working-tree churn during routine issuekit upgrades.
- Makes TUI and rollout actions clearer: global tool upgrade is separate from repo scaffold check/apply.

## Related Context

infra-toolkit currently has `issuekit-rollout --host pike3`, which updates the global issuekit tool and then runs `issuekit setup --json` across configured repos. For daily pike3 tool updates, infra-toolkit now uses `issuekit-upgrade`, but the full rollout path still lacks a read-only preflight from issuekit.
