# Adoption notes never reach the proposal sender

`issuekit adopt <id> --append-file <notes>` appends the notes to the **adopted
issue in the receiving project**. Nothing is sent back to the origin project.
The sender's only way to see the outcome is to poll `issuekit outgoing --to
<project>` themselves.

The triage decisions are adopt, adopt_and_reply, reply, or discard.
`adopt_and_reply` sends a linked follow-up only when the sender must act; it
never replies automatically to a proposal that is itself a reply. Ordinary
adoption notes still do not reach the sender.
Discard decisions remain pull-based; they are visible through `issuekit
outgoing` but do not send automatic notifications.

## The failure this causes

Writing anything the sender needs to act on into adoption notes silently loses
it. This has happened: a request for a follow-up report was written into
adoption notes for a proposal, the sender never received it, and the omission
was only noticed when a human asked why nothing had appeared. It was
compounded by a second effect below.

If the sender must know something, send a proposal. Do not rely on adoption
notes to carry it.

## `issuekit edit --body-file` replaces, it does not append

`--body-file` and `--body` overwrite the whole issue body; `--append` and
`--append-file` add to it. Re-scoping an adopted issue with `--body-file`
therefore deletes the adoption notes that `adopt` just wrote, including any
verification record. Use `--append-file`, or copy forward anything worth
keeping, when rewriting an adopted issue.

## Practical rules

- Adoption notes are for the receiving project's own record: verification
  performed, design decisions, scope calls.
- Anything directed at the sender goes in a proposal, with `--from-issue` so it
  gets a distinct origin. Proposals from one session that share the implicit
  `#0` origin are deduplicated; see [[proposal-origin-dedup]].
- After adopting a proposal that asked a question or offered follow-up work,
  check whether a reply proposal is owed before closing the loop.
