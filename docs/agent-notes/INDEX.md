# Agent notes index

One line per note. Add an entry when you add a note; remove it when you delete
one. See [README.md](README.md) for the rules.

- [Waiting on issuekit implement runs](waiting-on-implement-runs.md) - the run
  must be polled in the foreground; background waits do not observe completion.
- [ASCII-only review fields](ascii-only-review-fields.md) - `approve` and
  `submit-review` text fields reject non-ASCII characters.
- [CI policy](ci-policy.md) - which workflows are automatic and which are
  manual-only, and why.
- [One agent maps to one default protocol role](agent-roles-single-default.md) -
  `--agent` returns the wrong protocol when an agent serves two roles; pass
  `--role` instead.
- [MCP test coverage](mcp-test-coverage.md) - without the `mcp` dependency the
  whole MCP test file skips silently and the suite still reports green; keeping
  it in the dev group is deliberate.
- [Claude trust-dialog warning](claude-trust-dialog.md) - the untrusted-workspace
  permissions warning is cosmetic under the bypass-permissions launch mode.
- [Adoption notes never reach the proposal sender](adoption-notes-do-not-reach-the-sender.md) -
  `adopt --append-file` writes only to the receiving project's issue; anything
  the sender must act on needs a proposal. Also: `edit --body-file` replaces.
- [Multiple proposals from one session need distinct origins](proposal-origin-dedup.md) -
  without `--from-issue`, a second propose to the same target is silently
  deduplicated by the implicit `#0` origin.
- [Checking the mine-py API contract](mine-py-openapi-check.md) - the deployed
  `/openapi.json` is 404; generate it offline with `mine-export-openapi`, and
  probe the live server read-only when deployment state matters.
- [Verification-only issues cannot go through implement/submit](verification-only-issues.md) -
  a zero-diff agent run is not submitted and strands the claim; reclaim, then
  close with the no-op `complete --force` path.
- [Encoding check modes](encoding-check-modes.md) - use `check-encoding --gate`
  to reproduce submit behavior; the default command intentionally has broader
  whole-file and line-ending checks.
