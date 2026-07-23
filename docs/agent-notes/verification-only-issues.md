# Verification-only issues cannot go through implement/submit

`issuekit implement <id>` refuses to submit when the agent run produces no
worktree diff ("agent produced no implementation changes; not submitting for
review") and leaves the claim at `stage=implementing`.

For issues whose scope is verification with no expected code change, do not
route them through the implement/submit/review cycle. After the verification
evidence is collected:

1. `issuekit reclaim <id> --reason "verification-only run produced no diff"`
   to release the stuck claim.
2. `issuekit complete <id> --force --summary <evidence> --verification <cmds>`
   to close it as a verified no-op (the protocol's sanctioned no-op close).

Better: author such issues with an explicit note that the closer should use
the no-op complete path, or fold the verification into the upstream issue's
review instead of a standalone downstream issue.
