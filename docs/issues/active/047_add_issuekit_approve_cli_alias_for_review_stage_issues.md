---
id: 47
status: active
priority: medium
created: 2026-06-08
completed: 
stage: todo
author: claude
title: Add issuekit approve CLI alias for review-stage issues
---

# Issue #47: Add issuekit approve CLI alias for review-stage issues

## Problem

infra-toolkit reported that reviewer approval is implicit: to approve a
review-stage issue they used `issuekit complete <id>`, and the mapping from
"approve review" to "complete" is not obvious. The MCP server exposes an
`approve` tool, but the CLI has no `approve` command (it has `complete`,
`submit-review`, `request-changes`, `claim`, `queue`). This CLI/MCP asymmetry
makes the reviewer approval path harder to discover from the CLI.

Origin: infra-toolkit#0@10762a8.

## Proposed Solution

Add an `issuekit approve <id>` CLI command as a reviewer-friendly alias for
completing a review-stage issue.

1. `issuekit approve <id> --verification <text> [--summary <text>]
   [--reviewer <name>]` approves a review-stage issue, mirroring the MCP
   `approve` tool. Reuse the existing complete/approve workflow path; do not
   fork the logic.
2. Reject issues that are not at `stage=review` unless `--force` is supplied; a
   non-review issue should still go through `issuekit complete --force`.
3. Keep `issuekit complete` unchanged as the direct close / escape hatch.

## Impact

- `issuekit/cli.py`: register the `approve` subcommand and add it to the command
  list.
- `issuekit/commands/` (a new `approve.py`, or route to `complete` behind a
  review-stage guard): the command implementation.
- Reuse the existing review-stage and self-review guards (#35/#36); no new
  bypass.
- `tests/`: approve completes a review-stage issue, rejects a non-review stage
  without `--force`, and respects the self-review guard.

## Implementation Plan

1. Add the approve command reusing the existing complete/approve workflow path
   with a `stage=review` precondition.
2. Register it in `issuekit/cli.py`.
3. Add tests for accept, non-review-stage rejection, and the self-review guard.
4. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_approve_command.py`
- Manual: `issuekit approve <id> --verification "..."` on a review-stage issue
  completes it; on a non-review issue it errors without `--force`.
- `uv run issuekit validate`

## Related Resources

- Origin proposal: infra-toolkit#0@10762a8
- `issuekit/cli.py`, `issuekit/commands/complete.py`, `issuekit/workflow.py`
- MCP `approve` tool in `issuekit/mcp/server.py` (CLI parity target)
- Sibling adoption: the protocol/docs issue references this alias
