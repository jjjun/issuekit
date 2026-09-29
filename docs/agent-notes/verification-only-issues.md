# Verification-only issues cannot go through implement/submit

**Applies to:** `issuekit implement`, `issuekit reclaim`, `issuekit complete`
on issues whose scope is verification with no expected code change.

By default `issuekit implement <id>` refuses to submit when the agent run
produces no worktree diff ("agent produced no implementation changes; not
submitting for review") and leaves the claim at `stage=implementing`.

If the issue should still get a review, run it with
`issuekit implement <id> --allow-no-changes`. The run then submits for review
without a diff, and the reviewer decides from the implementer report. The same
flag is the recovery the error suggests when a resumed run finds a previous
attempt's unsubmitted edits.

To close a verified no-op without review, after the verification evidence is
collected:

1. `issuekit reclaim <id> --reason "verification-only run produced no diff"`
   to release the stuck claim. Without `--force`, reclaim refuses
   ("not currently flagged as an orphaned or stale claim") unless
   `issuekit orphans` lists the claim, which needs its recorded worker to be
   gone or silent for 300 seconds by default. Right after the failed run, or
   while a `serve` loop on that checkout keeps heartbeating, it is not listed;
   then add `--force`, which the CLI reserves for human-directed recovery.
   Reclaim returns the issue to the open pool, so run step 2 right away.
2. `issuekit complete <id> --force --summary <evidence> --verification <cmds>`
   to close it as a verified no-op (the protocol's sanctioned no-op close).

Do not skip step 1: while the claim is held, the issue still names the
implementer, and a forced complete can be refused as a self-review.

Better: author such issues with an explicit note that the closer should use
the no-op complete path, or fold the verification into the upstream issue's
review instead of a standalone downstream issue.
