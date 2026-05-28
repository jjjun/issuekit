---
id: 8
status: active
priority: high
created: 2026-05-29
completed:
title: Handle non-UTF-8 issue files gracefully in core
---

# Issue #8: Handle non-UTF-8 issue files gracefully in core

## Problem

`core.read_issues()` reads each issue file with
`file_path.read_text(encoding="utf-8-sig")` and no `errors=` handling. A single
file that is not valid UTF-8 (for example a legacy Shift_JIS / cp932 file whose
bytes include `0x83`) raises `UnicodeDecodeError` and crashes the command with a
full traceback. This affects every core command: `info`, `validate`,
`generate-indexes`, and `complete`.

This was found by running `issuekit info` against a repo that still had a
cp932-encoded issue file. `check_encoding` already tolerates this (it reads with
`errors="ignore"`), but the core reader does not.

## Proposed Solution

Never crash on an undecodable file. `validate` must report it as an error; the
other commands must continue without a traceback.

## Impact

- `issuekit/core.py` (`read_issues`)
- `issuekit/commands/validate.py`
- `tests/test_core.py`, `tests/test_validate.py`, `tests/fixtures/`

## Implementation Plan

1. In `core.read_issues`, read bytes and attempt a strict `utf-8-sig` decode.
   On `UnicodeDecodeError`, do not raise. Represent the file as an `Issue` with
   a `decode_error: bool = True` flag (add the field to the `Issue` dataclass),
   an empty `body`/`content`, and the filename-derived id/title so the file is
   still discoverable.
2. In `validate`, add a check: if `issue.decode_error`, append an error
   `"Issue file is not valid UTF-8: <relative_path>"`. This makes `validate`
   exit non-zero and name the offending file instead of crashing.
3. Ensure `info` and `generate-indexes` do not crash: undecodable files appear
   in counts/listings but contribute no body-derived data. `complete` must
   refuse clearly (non-zero, readable message) if the targeted id is an
   undecodable file, rather than tracebacking.
4. Consider whether `check-encoding` should also flag undecodable files (today
   it silently ignores decode errors via `errors="ignore"`). At minimum,
   document the division of responsibility: `check-encoding` = BOM/mojibake,
   `validate` = structural + decodability of issue files.

## Test Plan

- `uv run pytest tests/test_core.py tests/test_validate.py`.
- Add a fixture issue file with invalid UTF-8 bytes (e.g. cp932 content).
- Cover: `read_issues` returns it with `decode_error=True` and does not raise;
  `validate` reports it as an error and exits non-zero; `info` does not crash.

## Related Resources

- `issuekit/core.py` `read_issues` (line that calls `read_text("utf-8-sig")`)
- `issuekit/commands/check_encoding.py` (uses `errors="ignore"`, for comparison)
