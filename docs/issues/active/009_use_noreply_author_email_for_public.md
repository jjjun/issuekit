---
id: 9
status: active
priority: low
created: 2026-05-29
completed:
title: Use a noreply author email in pyproject for public release
---

# Issue #9: Use a noreply author email in pyproject for public release

## Problem

`pyproject.toml` lists a personal Gmail address as the package author email.
issuekit is being published as a public repository, so this address would be
visible to anyone. There are no other secrets in the repo; this is the only
piece of personal contact information exposed.

## Proposed Solution

Replace the author email with a noreply address before/at public release. Keep
the author name.

## Impact

- `pyproject.toml` (`[project].authors`)

## Implementation Plan

1. In `pyproject.toml`, change the `authors` email to a noreply address.
   Preferred: the GitHub noreply form for this account
   (`<id>+jjjun@users.noreply.github.com`), or a generic `noreply@...` the owner
   chooses. Confirm the exact value with the owner before committing.
2. Do not change the author name.

## Test Plan

- `pyproject.toml` still parses (`uv build` succeeds).
- No other occurrences of the old email remain in tracked files
  (`git grep` for the old address returns nothing).

## Related Resources

- `pyproject.toml`
- Context: issuekit going public (only repo names + this email are exposed; no secrets).
