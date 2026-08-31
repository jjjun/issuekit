# Multiple proposals from one session need distinct origins

When a session sends several unrelated proposals to the same target project
without `--from-issue`, every proposal gets the implicit origin
`<project>#0@<commit>`. The origin embeds the current HEAD commit, so two
propose calls only share an origin if no commit landed between them; a commit
in between means the calls do NOT collide even with the same implicit `#0`.
The server deduplicates by origin: when a propose call's origin matches a
pending proposal already on the target, the server returns that existing
proposal instead of creating a new one.

Recovery / correct flow:

- Pass `--from-issue <local-issue-id>` (CLI) or `from_issue=` (MCP `propose`)
  so each proposal derives a distinct origin such as `issuekit#286@<commit>`.
- If no motivating local issue exists yet, author it first (it can gain
  `depends_on` refs to the proposal afterwards via `update_issue`).
- Check the propose result for `deduplicated` before assuming the proposal
  was created; when `true`, the returned `id` belongs to the earlier
  proposal, not a new one. If the title or body also differs from what was
  sent, the CLI instead exits 1 with `payload_mismatch: true` and a warning,
  since that combination would silently lose the new content.
