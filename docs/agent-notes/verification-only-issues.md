# Verification-only issues cannot go through implement/submit

**Applies to:** `issuekit implement`, `issuekit show`, `issuekit orphans`,
`issuekit reclaim`, and `issuekit complete` on verification-only issues

By default `issuekit implement <id>` refuses to submit when the agent run
produces no worktree diff and leaves the claim at `stage=implementing`. If the
issue should still get a review, run it with
`issuekit implement <id> --allow-no-changes` to submit without a diff.

To close a verified no-op without review, use
these steps after collecting the verification evidence:

1. `issuekit reclaim <id> --reason "verification-only run produced no diff"`
   to release the claim. If `issuekit orphans` does not list it yet, add
   `--force`, for example right after the failed run or while a `serve` loop
   keeps heartbeating. Reclaim returns the issue to the pool, so run step 2
   right away.
2. `issuekit complete <id> --force --summary <evidence> --verification <cmds>`
   to close it as a verified no-op.

The complete request sends no reviewer or session, and the server can refuse
it as `forbidden_self_review`: `IssueWorkflowService.complete` resolves
`reviewer = issue.reviewer or issue.assignee` and calls
`_ensure_not_self_review`, while the claim leaves the assignee as the
implementer.

`issuekit orphans` uses worker heartbeats, not local run status. A `serve` loop
continues heartbeating and keeps its worker from being listed as stale; other
claims are reported according to worker availability and the 300-second
default heartbeat threshold. For a dead one-shot run, `issuekit show <id>
--json` reports `stale_run` when local run metadata identifies it as dead.
