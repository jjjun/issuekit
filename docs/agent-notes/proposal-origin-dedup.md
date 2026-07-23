# Multiple proposals from one session need distinct origins

When a session sends several unrelated proposals to the same target project
without `--from-issue`, every proposal gets the implicit origin
`<project>#0@<commit>`. The server deduplicates by origin: the second propose
call is silently not sent and returns the first proposal's payload with
`idempotent_existing: true`, `payload_mismatch: true`, and a warning.

Recovery / correct flow:

- Pass `--from-issue <local-issue-id>` (CLI) or `from_issue=` (MCP `propose`)
  so each proposal derives a distinct origin such as `issuekit#286@<commit>`.
- If no motivating local issue exists yet, author it first (it can gain
  `depends_on` refs to the proposal afterwards via `update_issue`).
- Check the propose result for `idempotent_existing` / `warning` before
  assuming the proposal was created; the returned `id` may belong to the
  earlier proposal.
