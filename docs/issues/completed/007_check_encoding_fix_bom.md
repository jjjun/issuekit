---
id: 7
status: completed
priority: high
created: 2026-05-29
completed: 2026-05-29
title: Add --fix to check-encoding to strip BOMs
---


# Issue #7: Add --fix to check-encoding to strip BOMs

## Problem

`check-encoding` detects BOM and mojibake but cannot fix anything. Adopting
issuekit in existing repos surfaces many pre-existing BOM files (a pilot repo had
48). Stripping them by hand is error-prone, and BOM removal is a safe,
deterministic transform that the tool should own.

Mojibake is different: it is semantic corruption that cannot be auto-corrected,
so `--fix` must never rewrite mojibake files.

## Proposed Solution

Add a `--fix` flag to `check-encoding` that rewrites BOM-flagged files as UTF-8
without a BOM. It strips the leading 3 BOM bytes only; it must not touch the rest
of the file.

## Impact

- `issuekit/cli.py` (add `--fix` to the check-encoding subparser)
- `issuekit/commands/check_encoding.py`
- `tests/test_check_encoding.py`

## Implementation Plan

1. Add `--fix` (store_true) to the `check-encoding` subparser in `cli.py`.
2. When `--fix` is set, for each BOM file: read the raw bytes, remove the leading
   `EF BB BF`, and write the remaining bytes back verbatim.
   - CRITICAL: operate on raw bytes (slice off the first 3 bytes). Do NOT decode
     to text and re-encode. A decode/encode round-trip risks introducing the
     exact transcription corruption this tool exists to catch, and could rewrite
     line endings. Preserve every byte after the BOM exactly.
3. Print `Fixed BOM: <file>` for each fixed file.
4. After fixing, re-evaluate the result:
   - BOM offenders are resolved by `--fix`.
   - Mojibake files are reported but NOT modified. If any mojibake remains, exit
     non-zero with the mojibake report; otherwise exit 0.
5. Without `--fix`, behavior is unchanged (detect only, non-zero on any offender).
6. `--json` with `--fix` includes a `fixed` list alongside `bom_files` and
   `mojibake_files`.

## Test Plan

- `uv run pytest tests/test_check_encoding.py`.
- Cover:
  - a file with a leading BOM is fixed: byte-level assertion that it no longer
    starts with `EF BB BF` and that the bytes after the original BOM are
    unchanged (including any non-ASCII content and CRLF, if present).
  - a mojibake file is reported but its bytes are unchanged under `--fix`.
  - exit code is 0 when only BOMs existed and were fixed; non-zero when mojibake
    remains.
  - `--json` shape includes `fixed`.

## Related Resources

- `issuekit/commands/check_encoding.py` (Issue #5)
- Depends on Issue #5.

**Completed**: 2026-05-29

## Completion Notes

- Added --fix to strip leading UTF-8 BOM bytes while preserving the rest of each file exactly.
- Verification: `uv run pytest; uv run issuekit validate; uv run issuekit check-encoding --json`
