---
id: 6
status: active
priority: medium
created: 2026-05-28
completed:
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
- New: `issuekit/templates/` (gitattributes, pre-commit config, issues README)
- New: `tests/test_init.py`

## Implementation Plan

1. Create `docs/issues/active/`, `docs/issues/completed/`, `docs/issues/indexes/`
   if missing.
2. Write `docs/issues/README.md` from a template if missing (mirror this repo's
   `docs/issues/README.md`).
3. Write `.gitattributes` from a template if missing (mirror this repo's
   `.gitattributes`; include language-appropriate extensions).
4. Write `.pre-commit-config.yaml` (or append a hook) that runs
   `issuekit check-encoding` on commit. If the file exists, do not clobber;
   print guidance instead.
5. Run `generate-indexes` so `indexes/` is valid immediately.
6. `--force` allows overwriting templated files. Without it, skip existing files
   and report what was skipped.
7. All written files must be UTF-8 without BOM and LF.

## Test Plan

- `uv run pytest tests/test_init.py`.
- Cover: fresh dir gets full scaffold + valid indexes; re-running is a no-op;
  existing files are preserved without `--force`; `--force` overwrites templates;
  written files have no BOM and no CRLF.

## Related Resources

- `docs/issues/README.md`, `.gitattributes` (templates to mirror)
- Depends on Issue #4 (generate-indexes).
