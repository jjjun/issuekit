---
id: 54
status: completed
priority: medium
created: 2026-06-09
completed: 2026-06-09
stage: done
author: claude
title: Centralize managed frontmatter keys, passthrough, and slug helpers
---

# Issue #54: Centralize managed frontmatter keys, passthrough, and slug helpers

## Problem

The set of managed issue-frontmatter keys (`id`, `status`, `priority`,
`created`, `completed`, `assignee`, `stage`, `implementer`, `author`, `title`)
is hard-coded in three separate places, and the passthrough helper that strips
those keys is duplicated verbatim:

- `issuekit/core.py` `format_issue_frontmatter` defines `fixed_keys`.
- `issuekit/workflow.py` `_passthrough_frontmatter` defines `managed_keys`.
- `issuekit/commands/complete.py` `_passthrough_frontmatter` defines an
  identical `managed_keys` and an identical function body.

If the frontmatter schema ever changes (a new managed key), three call sites
must be edited in lock-step or the trackers silently diverge. The two
`_passthrough_frontmatter` functions are byte-for-byte duplicates.

Separately, there are two near-identical slug functions that have drifted:
`issuekit/proposals.py` `slugify` (truncates to 64 chars, default `"proposal"`)
and `issuekit/commands/author.py` `_slugify` (no truncation, default
`"issue"`). They share the same two-pass regex normalization.

## Proposed Solution

1. Add a single `MANAGED_FRONTMATTER_KEYS` constant in `issuekit/core.py` and a
   shared `passthrough_frontmatter(data)` helper there. Have
   `format_issue_frontmatter`, `workflow._write_active_issue`, and
   `complete.complete_issue` all reference the one constant/helper.
2. Consolidate slug generation into one core helper (e.g. `slugify(value, *,
   default, max_len)`) and have `proposals.slugify` and `author._slugify` call
   it with their respective defaults, preserving current behavior (64-char cap
   and `"proposal"` default for proposals; no cap and `"issue"` default for
   authored issues) -- or deliberately unify the behavior and note the change.

## Impact

- `issuekit/core.py` (new constant + helpers)
- `issuekit/workflow.py` (use shared passthrough)
- `issuekit/commands/complete.py` (use shared passthrough)
- `issuekit/proposals.py` and `issuekit/commands/author.py` (use shared slug)
- Tests covering frontmatter round-tripping and slug generation.

## Implementation Plan

1. Introduce `MANAGED_FRONTMATTER_KEYS` and `passthrough_frontmatter` in
   `core.py`; refactor `format_issue_frontmatter` to derive `fixed_keys` from
   the constant.
2. Replace both `_passthrough_frontmatter` definitions with imports of the
   shared helper.
3. Add the shared slug helper and route both existing slug callers through it.
4. Keep existing observable behavior unless intentionally unifying; document any
   behavior change in the handoff note.

## Test Plan

- `uv run pytest tests/test_core.py tests/test_workflow.py tests/test_complete.py tests/test_proposals.py tests/test_author_command.py`
- `uv run pytest`
- `uv run issuekit validate`

## Related Resources

- `issuekit/core.py` `format_issue_frontmatter`
- `issuekit/workflow.py` `_passthrough_frontmatter`
- `issuekit/commands/complete.py` `_passthrough_frontmatter`
- `issuekit/proposals.py` `slugify`, `issuekit/commands/author.py` `_slugify`

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-09

## Completion Notes

- Centralize managed frontmatter keys, passthrough, and slug helpers.
- Verification: `Reviewed diff: added MANAGED_FRONTMATTER_KEYS constant and shared passthrough_frontmatter in core.py; format_issue_frontmatter derives fixed_keys from it; both duplicate _passthrough_frontmatter (workflow.py, complete.py) now call the shared helper. Added parameterized core.slugify(value, *, default, max_len); proposals.slugify and author._slugify wrap it preserving prior behavior (64-cap/'proposal'; no-cap/'issue'). Behavior verified equivalent. Full suite 274 passed/22 skipped; check-encoding clean; validate clean.`
