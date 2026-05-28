---
id: 6
status: completed
priority: medium
created: 2026-05-28
completed: 2026-05-29
title: Implement init command to scaffold a repo
---


# Issue #6: Implement init command to scaffold a repo

## Problem

Five Python repos already have a `docs/issues/` convention but no encoding guard
and no generated indexes. Onboarding each repo by hand is error-prone. There is
no equivalent in the reference implementation; this is new.

## Proposed Solution

Add `issuekit init` to scaffold the tracker and distribute the encoding guard
into a target repo. It must be idempotent: never overwrite existing files unless
`--force` is given.

## Impact

- New: `issuekit/commands/init.py`
- New: `issuekit/templates/` (gitattributes, editorconfig, pre-commit config,
  issues README)
- New: `tests/test_init.py`

## Implementation Plan

1. Create `docs/issues/active/`, `docs/issues/completed/`, `docs/issues/indexes/`
   if missing.
2. Write `docs/issues/README.md` from a template if missing (mirror this repo's
   `docs/issues/README.md`).
3. Write `.gitattributes` from a template if missing (mirror this repo's
   `.gitattributes`; include language-appropriate extensions).
4. Write `.editorconfig` from a template if missing (mirror this repo's
   `.editorconfig`: `charset = utf-8`, `end_of_line = lf`,
   `insert_final_newline = true`). This is defense-in-depth for human and editor
   edits; it does not bind codex, so it is not a substitute for steps 5-6.
5. Write `.pre-commit-config.yaml` (or append a hook) that runs
   `issuekit check-encoding` on commit. The pre-commit gate is required: it is
   the only mechanism that reliably stops encoding damage at the moment it is
   introduced. If the file exists, do not clobber; print guidance on the hook to
   add instead.
6. Run `generate-indexes` so `indexes/` is valid immediately.
7. `--force` allows overwriting templated files. Without it, skip existing files
   and report what was skipped.
8. All written files must be UTF-8 without BOM and LF.

### ascii_id_threshold for repos with legacy non-ASCII issues

`validate` requires ASCII-only content for issues with id >= `ascii_id_threshold`
(default 0 = all issues). Repos that already hold Japanese issues (e.g. mine-py)
would fail validation on every legacy issue under the default.

- `init` must detect the highest existing issue id in the target repo and, when
  legacy non-ASCII issues are present, write a `[tool.issuekit]` block to
  `pyproject.toml` (or print the exact block to add when there is no
  `pyproject.toml`) setting `ascii_id_threshold` to `max_existing_id + 1`.
- This grandfathers old issues while enforcing ASCII on all new ones.
- Never rewrite or "fix" existing non-ASCII issue bodies.

## Test Plan

- `uv run pytest tests/test_init.py`.
- Cover: fresh dir gets full scaffold (incl. `.editorconfig` and pre-commit) +
  valid indexes; re-running is a no-op; existing files are preserved without
  `--force`; `--force` overwrites templates; written files have no BOM and no
  CRLF.
- Cover ascii_id_threshold: a target repo with a legacy non-ASCII issue gets a
  `[tool.issuekit]` `ascii_id_threshold` set above the legacy ids, and
  `validate` then passes.

## Related Resources

- `docs/issues/README.md`, `.gitattributes`, `.editorconfig` (templates to mirror)
- `AGENTS.md` "Encoding rules (required)" (the rules this command operationalizes)
- Depends on Issue #4 (generate-indexes).

**Completed**: 2026-05-29

## Completion Notes

- Implemented init scaffolding with templates, idempotent writes, pre-commit guidance, and legacy ASCII threshold handling.
- Verification: `uv run pytest; uv run issuekit validate; uv run issuekit check-encoding --json`
