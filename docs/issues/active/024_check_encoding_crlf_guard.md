---
id: 24
status: in_progress
priority: medium
created: 2026-06-03
completed: 
assignee: codex
stage: implementing
implementer: codex
title: Add CRLF detection to check-encoding
---

# Issue #24: Add CRLF detection to check-encoding

## Problem

`issuekit check-encoding` inspects tracked source files for a leading UTF-8 BOM
and for mojibake, but it does not inspect line endings. CRLF passes the gate.

A repo `.gitattributes` with `* text=auto eol=lf` only normalizes on checkout
and when content passes the clean filter on add. Blobs committed as CRLF before
those rules existed, or added through a path that bypasses normalization, stay
CRLF. A one-time `git add --renormalize` fixes existing blobs but cannot prevent
reintroduction. Because the encoding gate ignores CRLF, the BOM and CRLF cases
are asymmetric: BOM is blocked mechanically at commit time, CRLF is not. Agents
(codex in particular) are prone to CRLF churn, which inflates diffs and obscures
review.

This adds a symmetric CRLF guard so CRLF reintroduction is caught by the same
gate that already catches BOM.

## Proposed Solution

Add CRLF detection to `check-encoding`, enabled by default, alongside the
existing BOM and mojibake checks.

Base the decision on what git stores in the index, not on working-tree bytes.
This is mandatory for correctness: on a Windows checkout with
`core.autocrlf=true`, a blob stored as LF appears as CRLF in the working tree, so
scanning working-tree bytes for CR would produce false positives on every file.
Use `git ls-files --eol -z`, whose `i/` column reports the eol of the index blob.

Detection rule: flag a tracked file when its index eolinfo (`i/`) is `crlf` or
`mixed`. This rule automatically respects `.gitattributes`:

- A file declared `eol=crlf` (for example a Windows `*.bat`) is stored by git as
  an LF blob and materialized as CRLF only on checkout, so its index eolinfo is
  `i/lf`. It is never flagged.
- A binary file (`-text`) reports `i/-text`, never `i/crlf`, so binary is out of
  scope without an extension list.
- A genuine CRLF text blob with no `eol=crlf` declaration reports `i/crlf` (or
  `i/mixed` for mixed endings) and is flagged.

As an explicit safeguard and to document intent, also skip any entry whose
`attr/` field contains `eol=crlf`, even though such entries are expected to be
`i/lf` in practice.

Note the deliberate split in how target files are selected: BOM and mojibake
continue to use the `SOURCE_EXTENSIONS` allowlist (working-tree byte scan), while
CRLF uses git's own text/binary classification from `git ls-files --eol`. The
CRLF check does not consult `SOURCE_EXTENSIONS`; `.gitattributes` is the single
source of truth for which files must be LF. This is intentional and matches the
goal of centralizing line-ending policy in `.gitattributes`.

CRLF is detection-only. `--fix` must not rewrite line endings; renormalization is
git's job (`git add --renormalize .`). Keep the existing BOM `--fix` behavior
exactly as is, including that it preserves any bytes (including CR) after the BOM.

issuekit has no external consumers yet, so the `--json` schema may change freely;
no backward-compatibility shim is required.

## Impact

- `issuekit/cli.py` (add `--no-crlf` to the check-encoding subparser)
- `issuekit/commands/check_encoding.py` (CRLF detection, output, `--json` key)
- `tests/test_check_encoding.py` (new cases; update the exact-match `--json`
  assertions to include the new key)
- `docs/issues/README.md` (the closing note that describes what
  `check-encoding` checks)

## Implementation Plan

1. Add `--no-crlf` (`store_true`) to the `check-encoding` subparser in `cli.py`,
   mirroring `--no-mojibake`.
2. In `check_encoding.py`, add a helper that returns the list of CRLF-violating
   files from git index metadata:
   - Run `git ls-files --eol -z` in `Path.cwd()` (same cwd as the existing
     `list_tracked_files`).
   - Split the output on NUL. Each record has the form
     `i/<eol> w/<eol> attr/<attrs><TAB><path>`.
   - For each record, split once on the TAB to recover `<path>` (the metadata
     part may contain spaces, e.g. `attr/text eol=crlf`, so do not split the
     path off by whitespace).
   - Parse the first whitespace-separated token of the metadata as `i/<eol>`.
   - Flag the path when `<eol>` is `crlf` or `mixed`, unless the `attr/` token
     contains `eol=crlf`.
   - Return paths in the order git reports them.
3. In `run`, when `not args.no_crlf`, compute `crlf_files` from the helper. Run
   it unconditionally relative to the BOM/mojibake loop (it is a single git call,
   not a per-file read), and guard only on the `--no-crlf` flag.
4. Update the pass/fail logic so the command fails (exit 1) when any of
   `bom_files`, `mojibake_files`, or `crlf_files` is non-empty. Preserve the
   existing BOM `--fix` resolution: `--fix` still strips BOMs and CRLF is
   unaffected by `--fix`.
5. Add a `crlf_files` key to the `--json` payload alongside `bom_files`,
   `mojibake_files`, and `fixed`.
6. Add non-JSON stderr reporting for CRLF, consistent with the BOM/mojibake
   blocks: a summary count line plus one indented line per offending file, and a
   short tip pointing to `git add --renormalize .`.
7. Update the success message wording so a clean run reflects that CRLF was also
   checked.
8. Update the final note in `docs/issues/README.md` so it states that
   `check-encoding` checks BOM, mojibake, and CRLF.

## Test Plan

- `uv run pytest tests/test_check_encoding.py`
- `uv run issuekit validate`
- `uv run issuekit check-encoding`
- New/updated cases (build fixtures with `git init`, `core.autocrlf=false`, and
  `git add` so the index blob keeps the intended endings):
  - A tracked text file whose blob has CRLF and no `eol=crlf` attribute fails;
    the path appears in stderr and in `crlf_files`.
  - A file declared `eol=crlf` in `.gitattributes` does not fail (its index blob
    is LF).
  - A binary file (`-text`) with CR bytes does not fail.
  - A mixed-ending blob (`i/mixed`) fails.
  - `--no-crlf` suppresses the CRLF failure while BOM/mojibake still fail.
  - Update the two exact-match `--json` assertions
    (`test_check_encoding_json_shape`, `test_check_encoding_fix_json_reports_fixed_files`)
    to include the `crlf_files` key.
  - A path containing a space is parsed correctly (TAB-delimited path).

## Related Resources

- `issuekit/commands/check_encoding.py` (Issue #5, Issue #7)
- `issuekit/cli.py` (check-encoding subparser)
- mine-js-monorepo proposal 025 (CRLF guard), issue 450 (one-time renormalize),
  proposal 022 (shared issuekit CLI / encoding guard)
- git: `git ls-files --eol` reports `i/<eol>` for the index blob; `eol=crlf`
  files are stored as LF blobs, so the rule respects `.gitattributes` by design.
