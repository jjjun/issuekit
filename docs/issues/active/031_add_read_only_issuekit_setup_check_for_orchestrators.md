---
id: 31
status: in_progress
priority: medium
created: 2026-06-05
completed: 
assignee: codex
stage: review
implementer: codex
origin: infra-toolkit#0@7522a33
title: Add read-only issuekit setup check for orchestrators
---

# Issue #31: Add read-only issuekit setup check for orchestrators

## Problem

The stable `issuekit setup --json` command applies the repo scaffold before
reporting diagnostics. That fits onboarding but is too strong for routine
orchestration: a read/check pass can still touch repo files such as generated
indexes or missing MCP scaffold files.

This leaves an orchestrator (infra-toolkit) with an awkward choice: always run
the applying setup command across every configured repo, or reimplement
issuekit-owned scaffold checks externally. Reimplementing duplicates issuekit
policy and drifts from the source of truth.

## Proposed Solution

Add an issuekit-owned read-only setup check and clarify the apply boundary:

- Add `issuekit setup check --json` (or `issuekit setup --check --json`).
- The check must not write files and must not run subprocesses.
- Report whether repo scaffold is current, missing, stale, or blocked.
- Keep applying behavior as `issuekit setup --json`, optionally with an explicit
  `issuekit setup apply --json` alias.
- Emit machine-readable fields: `ok`, `needs_setup`, `would_write`,
  `would_update`, `diagnostics`, `actions`.
- Keep scaffold ownership inside issuekit. Consumers must not inspect
  `.mcp.json`, `.codex/config.toml`, AGENTS.md, CLAUDE.md, or index templates
  directly.

## Impact

- New read-only CLI surface for setup checks.
- Existing `issuekit setup --json` apply behavior preserved.
- Orchestrators call apply only where the check reports setup is needed,
  avoiding unnecessary working-tree churn during routine upgrades.

## Implementation Plan

1. Add a read-only check path that computes scaffold state without writing or
   spawning subprocesses.
2. Expose it as `issuekit setup check --json` with the fields above.
3. Keep/alias the applying path and document the check/apply boundary.

## Test Plan

- `issuekit setup check --json` on a current, missing, and stale repo returns
  the expected `needs_setup` / `would_write` / `would_update` values and writes
  no files.
- `issuekit validate`

## Related Resources

- Origin proposal: `infra-toolkit#0@7522a33`
- Intended consumer: infra-toolkit `issuekit-rollout` preflight

## Handoff

- Summary: Added a read-only setup check path exposed as issuekit setup check --json and issuekit setup --check --json, plus setup apply --json as an explicit apply alias. The check reports ok, state, needs_setup, would_write, would_update, diagnostics, and actions without calling init_repo, writing files, or running subprocesses. It compares MCP handoff scaffold and generated indexes using issuekit-owned logic, documents the check/apply boundary, and adds tests for current, missing, stale, blocked, no-write, and no-subprocess behavior. Verification: uv run pytest; uv run issuekit validate; uv run issuekit check-encoding.
- Branch: `main`
- Commit: `4782142`
