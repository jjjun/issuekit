---
id: 14
status: active
priority: high
created: 2026-06-01
title: Fix blank-line growth in workflow issue body rewrites
---


# Issue #14: Fix blank-line growth in workflow issue body rewrites

## Problem

Every workflow transition that rewrites an active issue inserts one extra blank
line between the frontmatter and the body. Running claim -> submit-review ->
request-changes -> submit-review and then complete grows the gap from one blank
line to many, and the completed file inherits the accumulated blanks.

Observed on a fresh issue (body shown as repr, `\n` are real newlines):

```
ORIGINAL          ...---\n\n# Issue ...        (1 blank line)
AFTER CLAIM       ...---\n\n\n# Issue ...      (2 blank lines)
AFTER SUBMIT      ...---\n\n\n\n# Issue ...    (3 blank lines)
AFTER REQUEST     ...---\n\n\n\n\n# Issue ...  (4 blank lines)
AFTER COMPLETE    ...---\n\n\n\n\n\n\n# Issue  (the completed file keeps them)
```

Root cause: `issuekit/core.py` `format_issue_frontmatter` already returns a
trailing blank line (it ends with `---\n\n`). `issuekit/workflow.py`
`_write_active_issue` then concatenates `frontmatter.body`, but
`parse_issue_frontmatter` returns the body with its own leading `\n`
(`Frontmatter.body` starts at the character right after the closing fence
newline). The function calls `.rstrip()` on the body tail but never strips the
leading newline, so `format(...)` trailing `\n\n` plus the body's leading `\n`
stack up by one blank line per rewrite. `issuekit/commands/complete.py`
`complete_issue` builds its content the same way and so inherits the same drift.

This does not fail `issuekit validate` (extra blank lines are allowed), and the
current tests only assert substring presence (for example `"## Handoff" in
content`), so no existing test catches it. The harm is noisy, growing diffs on
every transition and unstable completed-file output.

## Proposed Solution

Normalize the boundary between the formatted frontmatter and the body in exactly
one place so a rewrite is idempotent: re-serializing an unchanged issue must be
byte-for-byte identical, and repeated transitions must not change the blank-line
count between the frontmatter and the first body line.

Keep the existing field order and the single blank line that
`format_issue_frontmatter` emits after the closing fence. The body must
contribute no leading blank lines of its own.

## Impact

- Modified: `issuekit/workflow.py` (`_write_active_issue` body concatenation)
- Modified: `issuekit/commands/complete.py` (`complete_issue` body
  concatenation, same boundary normalization)
- Modified: `tests/test_workflow.py`, `tests/test_workflow_cli.py`,
  `tests/test_complete.py` (add blank-line / round-trip assertions)

## Implementation Plan

1. In `issuekit/workflow.py` `_write_active_issue`, strip leading newlines from
   the parsed body before concatenation so the only blank line between the
   frontmatter and the body is the one from `format_issue_frontmatter`. For
   example derive the body as `frontmatter.body.strip("\n")` (or `lstrip("\n")`
   plus the existing tail handling), then build the file as
   `f"{format_issue_frontmatter(data)}{body}\n"`. Preserve the appended
   `## Handoff` / `## Review Feedback` note spacing (one blank line before the
   heading).
2. Apply the identical boundary normalization in
   `issuekit/commands/complete.py` `complete_issue` so completed files do not
   inherit accumulated blanks. The completion note formatting in
   `_append_completion_note` must be unchanged otherwise.
3. Do not change `format_issue_frontmatter`; the fix belongs at the
   concatenation sites so the frontmatter format stays stable for all callers.
4. All writes continue to go through `write_issue_atomic`; no CRLF, no BOM.

## Test Plan

- `uv run pytest tests/test_workflow.py tests/test_workflow_cli.py
  tests/test_complete.py`
- Idempotence: claiming an already-claimed issue (or any no-op re-serialize)
  produces a byte-identical file.
- No growth: run claim -> submit-review -> request-changes -> submit-review and
  assert the substring `---\n\n\n` never appears (exactly one blank line between
  the closing frontmatter fence and the first body line at every step).
- Completion: after `complete_issue`, assert the completed file has exactly one
  blank line between the frontmatter and the body, and still contains the
  handoff/feedback/completion notes.
- Confirm the body content (headings, notes) is otherwise unchanged.
- Run full `uv run pytest` and `uv run issuekit validate` to confirm no
  regression.

## Related Resources

- `issuekit/workflow.py` (`_write_active_issue`)
- `issuekit/commands/complete.py` (`complete_issue`)
- `issuekit/core.py` (`format_issue_frontmatter`, `parse_issue_frontmatter`)
- Issue #11 (introduced the workflow rewrite path)
- Issue #10 (Test Plan called for byte-for-byte round-trip; this adds the
  missing coverage)
