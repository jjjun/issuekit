---
id: 28
status: completed
priority: medium
created: 2026-06-04
completed: 2026-06-04
stage: done
title: Show valid incoming proposals in issuekit info
---

# Issue #28: Show valid incoming proposals in issuekit info

## Problem

`issuekit info` reports active and completed issues, the next issue id, and
index health, but it says nothing about incoming cross-project proposals. An
operator running `issuekit info` to survey tracker state cannot see that there
are pending proposals waiting for triage; they have to separately run
`issuekit incoming` (or the MCP `list_incoming` tool) to discover them. Since
`info` is the documented first step before creating or reorganizing issues
(`docs/issues/README.md`, "Quick Start For Agents"), pending proposals should be
visible there too.

"Valid" here means proposals still awaiting triage: files directly under
`docs/issues/incoming/`, excluding those already moved to `incoming/adopted/` or
`incoming/discarded/`. `issuekit.proposals.list_incoming` already returns exactly
this set (it globs `incoming/*.md` non-recursively), so no new filtering logic is
needed.

## Proposed Solution

Extend the `info` command to load incoming proposals via
`list_incoming(issues_dir)` and surface them in both the JSON and text output.
This is a read-only display change; it does not adopt, move, or validate
proposals and does not touch the issue lifecycle.

JSON output: add a top-level `incomingProposals` array, each entry with:

- `origin` (e.g. `mine-js-monorepo#0@f8b6c5b3`)
- `title`
- `created`
- `file` (path relative to the issues dir, e.g. `incoming/<name>.md`)

Keep the existing `counts` object scoped to issue files (active/completed/total)
so its meaning does not change; the proposal count is derivable from the array
length.

Text output: add a header line and a detail section.

- Add a line in the status block, after `Latest completed id`:
  `- Incoming proposals: N`
- When `N > 0`, print an `Incoming proposals` section after the `Active issues`
  section, one line per proposal:
  `- <origin>: <title> (<file>)`

When there are no incoming proposals, the new array is empty, the header line
reads `0`, and no detail section prints. The `incoming/` directory may not exist;
`list_incoming` already returns `[]` in that case, so existing output is
unchanged when no proposals are present.

## Impact

- Modified: `issuekit/commands/info.py` (load proposals, extend summary dict,
  print header line and detail section)
- Modified: `tests/test_info.py` (cover the new JSON field and text section)
- No change to `info`'s exit code, the `counts` semantics, JSON keys for
  existing fields, or any other command.

## Implementation Plan

1. In `issuekit/commands/info.py`, import `list_incoming` from
   `issuekit.proposals` and call it with `issues_dir`.
2. Build a list of proposal dicts with `origin`, `title`, `created`, and `file`.
   Compute `file` as the proposal path relative to `issues_dir` using
   `Path(proposal.file_path).relative_to(issues_dir).as_posix()` so it reads like
   `incoming/<name>.md`, consistent with how `activeIssues[].file` is formatted.
3. Add `"incomingProposals": [...]` to the `summary` dict. Leave `counts`
   unchanged.
4. In the text branch, print `- Incoming proposals: {len(...)}` right after the
   `Latest completed id` line.
5. After the existing `Active issues` block, if `summary["incomingProposals"]`
   is non-empty, print an `Incoming proposals` header followed by one
   `- {origin}: {title} ({file})` line per proposal.
6. Keep all strings ASCII. Do not change the `--json` indentation or key order of
   existing fields.

## Test Plan

- Extend `tests/test_info.py`:
  - Write an incoming proposal file under
    `docs/issues/incoming/<name>.md` (reuse the proposal frontmatter shape from
    `docs/issues/README.md`: `origin`, `to`, `reply_to`, `created`, `title`).
    Assert `info --json` `incomingProposals` contains one entry with the
    expected `origin`, `title`, `created`, and `file == "incoming/<name>.md"`.
  - Assert `info` text output contains `Incoming proposals: 1` and a line with
    the proposal origin and title.
  - Confirm existing `make_issue_tree` based tests still pass: with no
    `incoming/` directory, `incomingProposals` is `[]` and the header reads
    `Incoming proposals: 0` with no detail section.
  - Optional: a proposal under `incoming/adopted/` or `incoming/discarded/` is
    NOT listed (guards the non-recursive "valid only" semantics).
- Run `uv run pytest tests/test_info.py`, then the full suite `uv run pytest`.
- Run `uv run issuekit validate` and `uv run issuekit check-encoding`.

## Related Resources

- `issuekit/commands/info.py` (`run` builds the summary dict and text output)
- `issuekit/proposals.py` (`list_incoming` L55, `Proposal` dataclass L24 with
  `file_path` and `file_name`)
- `tests/test_info.py`, `tests/issue_helpers.py` (`make_issue_tree`)
- `docs/issues/README.md` ("Quick Start For Agents", "Cross-Project Proposals")

## Handoff

- Summary: Added incoming proposal reporting to issuekit info JSON and text output, with tests for pending and triaged proposal handling.
- Branch: `main`
- Commit: `6753a4e`

**Completed**: 2026-06-04

## Completion Notes

- Approved by codex.
- Verification: `Reviewed diff at commit 6753a4e. info.py now calls list_incoming(issues_dir) and adds a top-level incomingProposals array (origin, title, created, file) without changing counts; file is computed via _proposal_relative_path using relative_to(issues_dir).as_posix() with a None-safe guard. Text output adds '- Incoming proposals: N' after the latest-completed line and an 'Incoming proposals' detail section only when non-empty, ASCII only. Tests cover empty case, JSON listing with exact file name, text listing, exclusion of triaged (adopted) proposals, and the 0-count/no-section path in the index-mismatch test. Ran 'uv run pytest' (167 passed, 18 skipped), 'uv run issuekit validate' (28 files, 0 warnings), and 'uv run issuekit check-encoding' (clean).`
