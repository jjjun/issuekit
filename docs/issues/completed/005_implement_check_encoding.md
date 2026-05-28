---
id: 5
status: completed
priority: high
created: 2026-05-28
completed: 2026-05-29
title: Implement check-encoding command with mojibake scan
---


# Issue #5: Implement check-encoding command with mojibake scan

## Problem

codex frequently introduces UTF-8 BOM and CRLF damage. A BOM is invisible to
ripgrep, so it must be checked at the byte level. The reference is
`../mine-js-monorepo/scripts/check-encoding.mjs`, which checks BOM only.

## Proposed Solution

Port `check-encoding.mjs` to `issuekit/commands/check_encoding.py` and extend it
with an optional mojibake scan, since several consuming repos contain mixed
Japanese text.

## Impact

- New: `issuekit/commands/check_encoding.py`
- New: `tests/test_check_encoding.py`

## Implementation Plan

1. Enumerate tracked files via `git ls-files -z` (handle the NUL separator).
2. Filter by source extensions (port the set from `check-encoding.mjs`; add
   `.py`, `.toml`, `.cfg`, `.ini`, `.txt`).
3. For each file, read the first 3 bytes; flag if equal to `EF BB BF` (BOM).
4. Mojibake scan: flag files whose decoded text matches the mojibake pattern
   from `core.py`. Make this togglable; default on.
5. `--json` outputs `{ "bom_files": [...], "mojibake_files": [...] }`.
6. Exit non-zero if any offender is found, with a per-file report and the
   `head -c 3 <file> | xxd` tip. Exit zero when clean.
7. Do NOT fail on CRLF: `.gitattributes` normalizes line endings on commit.

## Test Plan

- `uv run pytest tests/test_check_encoding.py`.
- Cover: clean tree passes; a file with a leading BOM fails; a file with mojibake
  fails; non-source extensions are ignored; `--json` shape.
- Use a temporary git repo fixture so `git ls-files` works.

## Related Resources

- `../mine-js-monorepo/scripts/check-encoding.mjs`
- Mojibake pattern from Issue #2 `core.py`.

**Completed**: 2026-05-29

## Completion Notes

- Implemented check-encoding with BOM and mojibake scans.
- Verification: `uv run pytest tests/test_check_encoding.py tests/test_core.py tests/test_cli.py; uv run issuekit check-encoding --json`
