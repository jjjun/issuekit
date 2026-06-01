---
id: 19
status: active
priority: medium
created: 2026-06-01
completed:
title: Add machine-readable --json output to issuekit setup
---


# Issue #19: Add machine-readable --json output to issuekit setup

## Problem

`issuekit setup` (issue #18) prints a human-readable scaffold result and a
diagnostics checklist. An external orchestrator (the infra-toolkit issuekit
rollout, which runs `setup` across many repos and hosts) has to scrape that text
to decide whether a repo is correctly wired. Text scraping is brittle: the
checklist wording can change, and aggregating pass/fail across many repos by
substring match is error-prone.

issuekit owns "how to set up one repo"; the orchestrator owns "which repos and
hosts". For the orchestrator to aggregate results cleanly, `setup` needs a
stable, structured output contract.

## Proposed Solution

Add `issuekit setup --json` that emits a single JSON object describing the
scaffold actions and the diagnostics, instead of the human checklist. The JSON
is the stable contract the orchestrator consumes; the default (no `--json`)
human output is unchanged. Reuse the existing `collect_diagnostics` data
(`Diagnostic` already has `status`, `label`, `details`), so the command adds no
new diagnostic logic.

## Impact

- Modified: `issuekit/commands/setup.py` (build and print a JSON payload when
  `--json` is set)
- Modified: `issuekit/cli.py` (add `--json` to the `setup` subparser)
- Modified: `README.md` (document `setup --json` as the automation contract)
- New: `tests/test_setup.py` cases for the JSON shape

## Implementation Plan

1. Add `--json` to the `setup` subparser in `issuekit/cli.py` (store_true),
   forwarded to `setup.run`.
2. In `issuekit/commands/setup.py`:
   - Keep `init_repo(..., with_mcp=True)` running as today (the scaffold still
     happens; `--json` only changes how results are reported).
   - When `--json` is set, build a dict and print `json.dumps(payload, indent=2)`
     instead of the human checklist and guidance. Suggested shape:
     ```json
     {
       "ok": true,
       "scaffold": {"written": [...], "skipped": [...], "guidance": [...]},
       "diagnostics": [
         {"status": "OK", "label": "...", "details": ["..."]}
       ]
     }
     ```
     `ok` is `true` when no diagnostic has status `ACTION` (repo-side wiring is
     complete). Derive `diagnostics` directly from `collect_diagnostics(cwd)` so
     there is one source of truth.
   - ASCII-only output; the JSON must round-trip through `json.loads`.
   - Exit code stays 0 on a successful scaffold (matching #18); `ok: false` in
     the payload signals "optional/global steps remain", not a process failure.
3. Do not change the default human output path.
4. Update `README.md`: `setup --json` is the contract for orchestration (for
   example the infra-toolkit rollout in infra-toolkit #67).

## Test Plan

- `uv run pytest tests/test_setup.py`
- `setup --json` on an empty repo prints valid JSON (`json.loads` succeeds) with
  `scaffold.written` containing `.mcp.json` and a `diagnostics` list; `ok` is a
  boolean.
- After a full scaffold with `issuekit-mcp` available (monkeypatched), every
  diagnostic has status `OK` and `ok` is `true`.
- With a missing `.codex/config.toml` (or forced `issuekit-mcp` absent), at least
  one diagnostic has status `ACTION` and `ok` is `false`.
- `diagnostics` entries match `collect_diagnostics` (same labels/statuses), so
  the JSON cannot drift from the human checklist.
- Default `setup` (no `--json`) output is unchanged (human checklist still
  printed; assert a known line still appears).
- Output is ASCII-only. Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/commands/setup.py` (`run`, `collect_diagnostics`, `Diagnostic`)
- `issuekit/cli.py` (`setup` subparser)
- Issue #18 (added `setup` and the diagnostics)
- infra-toolkit #67 (the rollout orchestrator that consumes this JSON)
