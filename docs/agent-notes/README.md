# Agent notes

This directory is working memory for agents operating in this repo. Unlike
[`../guides/`](../guides), which is reviewed documentation for humans, these
notes exist so an agent can record what it learned while doing the work and so
the next agent does not have to rediscover it.

## Rules

- **Read freely.** Skim [`INDEX.md`](INDEX.md) at the start of a task and open
  whatever looks relevant. No permission needed.
- **Write freely.** If you learned something operational that is not obvious
  from the code, git history, or the guides, write it down. No issue and no
  review are required to add or edit a note.
- **One topic per file.** Kebab-case filename, `.md` extension, flat in this
  directory. Add a one-line pointer to [`INDEX.md`](INDEX.md).
- **Correct in place.** If a note is wrong or stale, fix or delete it rather
  than appending a contradiction. These notes are memory, not a changelog.
- **Notes are committed**, so they are shared with the whole team and every
  other checkout. Keep them free of secrets, tokens, absolute paths containing
  usernames, and machine-specific values.
- **Write English ASCII**, matching the rest of the repo, and keep files UTF-8
  without BOM with LF endings so `issuekit check-encoding` stays clean.

## What belongs here

Operational knowledge that has to be remembered, for example:

- A command that behaves differently than its help text suggests.
- A workflow step that is easy to get wrong, and the symptom when you do.
- Environment quirks that cost time to diagnose.
- Repo-specific policy decisions that are not captured in config.

## What does not belong here

- **How to use issuekit.** That is [`../guides/`](../guides).
- **The handoff protocol.** `issuekit protocol` is the source of truth.
- **Anything the code or git history already records.** Do not narrate past
  fixes; a note should save future work, not log completed work.
- **Per-task scratch state.** Use the issue tracker for that.

## Note format

```markdown
# <Title>

**Applies to:** <command, subsystem, or situation>

<What to know, and what happens if you get it wrong.>
```
