# Issue Tracker

`docs/issues/` is the local issue system for this repository. It is also the
reference specification that `issuekit` enforces across all consuming repos.

Do not confuse this local tracker with GitHub Issues.

## Quick Start For Agents

Before creating, completing, or reorganizing issues, run:

```bash
issuekit info
```

After changing issue files, run:

```bash
issuekit generate-indexes
issuekit validate
```

The normal close path is reviewer approval (via `approve` in the MCP flow).  To
close an issue directly without review, use the completion command with the
explicit escape hatch:

```bash
issuekit complete <id> --force --summary "Completed scope." --verification "issuekit validate"
```

Do not hand-edit files under `docs/issues/indexes/`.

## Language And Encoding

All issue files must be written in English using ASCII characters only. This
applies to frontmatter, headings, body text, progress notes, completion notes,
summaries, and verification notes. This keeps issue files readable across
shells, editors, and AI agents.

## Directory Layout

```text
docs/issues/
  README.md                  # this specification
  active/                    # open, planned, investigating, or in-progress issues
  completed/                 # completed issue source files
  incoming/                  # cross-project proposals, ignored by validation
  indexes/                   # generated issue indexes; never edit by hand
    active.md
    completed-recent.md
    completed-001-099.md
    ...
```

`README.md` does not contain the full issue table. Generated indexes are split
so the system stays usable as completed issues grow.

External agent runs may create `.agent-runs/` at the repository root. The
directory is gitignored run-log storage, not issue state. Keep it out of
commits, but use the logs when reviewing how an orchestrated agent handled an
issue.

## Cross-Project Proposals

Related repositories can send suggestions as Markdown files in
`docs/issues/incoming/`. These proposal files are outside the issue lifecycle
until someone adopts them, so tracker validation, generated indexes, and claim
commands ignore them.

Related repo refs resolve from a shared workspace registry plus optional
per-repo local overrides. For sibling repos, place one `issuekit.workspace.toml`
above them:

```toml
[projects]
basekit = "basekit"
fast-domain = "fast-domain"
issuekit = "issuekit"
mine-py = "mine-py"
```

`issuekit` discovers the nearest workspace file by walking up from the current
directory. `ISSUEKIT_WORKSPACE` can point to an explicit workspace file and
overrides discovery. Relative `[projects]` paths resolve against the workspace
file's directory. Absolute paths are allowed for repos outside the workspace.

Each repo can still use gitignored `issuekit.local.toml` with a `[refs]` table
for private refs or overrides:

```toml
[refs]
fast-domain = "C:/abs/path/to/fast-domain"
```

Effective refs are loaded as workspace projects, then local refs; local entries
win on name conflicts. Use `issuekit add-ref <name> --path <repo>` for local
refs, `issuekit add-ref <name> --path <repo> --scope workspace` for the shared
file, and `issuekit list-refs` to inspect the merged view.

When work in one repo reveals a needed change that belongs to another
registered repo, use `issuekit list-refs` to find the ref and
`issuekit propose --to <ref> --title <t> --body <b>` to send a
non-destructive inbound suggestion for that repo to triage.

Proposal files use this ASCII frontmatter:

```markdown
---
origin: mine-py#42@abc123
to: fast-domain
reply_to:
created: 2026-06-03
title: Short proposal title
---

# Proposal: Short proposal title

## Context

## Suggested Change

## Rationale
```

`origin` is a stable source identifier in the form `<ref>#<id>@<commit>`.
`reply_to` is empty for an initial proposal and set to the original `origin`
when the proposal is a reply. Proposal text carries content, not remote status.
A reply is a new inbound proposal in the target repo.

Use `issuekit incoming` to list proposals, `issuekit adopt <proposal-file>` to
create a local active issue, and `issuekit discard <proposal-file>` to move a
proposal to `incoming/discarded/`. Adopted issues record the source `origin:` in
frontmatter and under Related Resources.

### MCP and CLI parity (non-MCP fallback)

The proposal-system MCP tools and the CLI call the same implementation, so the
CLI is a drop-in fallback when the MCP server is unstable (for example when a
cross-repo write hangs under an agent harness). Add `--json` to get the same
structured output the MCP tools return:

| MCP tool | CLI equivalent |
| --- | --- |
| `propose(to, title, body)` | `issuekit propose --to <ref> --title <t> --body <b> --json` |
| `list_incoming()` | `issuekit incoming --json` |
| `adopt_proposal(file, priority)` | `issuekit adopt <file> --priority <p> --json` |

`propose` also accepts `--body-file <path>` instead of `--body`, and
`--from-issue <id>` / `--reply <id>` to derive the body and destination from a
local issue. If an MCP proposal tool hangs or errors, run the matching CLI
command; the result is identical.

For `issuekit propose --reply <id>`, the destination ref is derived from the
recorded `origin` value unless `--to <name>` is also provided. The derived ref
name is the origin text before `#`, so shared project names should match repo
directory names. With that convention, replies work across all registered repos
without per-repo ref bookkeeping.

Proposal de-duplication uses the full `origin`, including `@commit`. Sending the
same source issue again after a new commit creates a separate proposal file.

## Issue Metadata

New issues must use YAML frontmatter. This ASCII frontmatter is the source of
truth for scripts and agents.

```yaml
---
id: 1
status: active
priority: medium
created: 2026-05-28
completed:
author: codex
title: Short issue title
---
```

Allowed `status` values: `active`, `planned`, `investigating`, `in_progress`,
`completed`.

Allowed `priority` values: `high`, `medium`, `low`.

Workflow tools may add optional `author`, `assignee`, `stage`, and
`implementer` fields. `author` records the agent that wrote the issue.
`implementer` records the agent that claimed the implementation so review can
reject self-review. An implementer may not explicitly assign itself as reviewer
at submit time; omit `reviewer` to route the issue through the open review pool,
where another session or agent (including one with the same name) may review it.
Agents should not hand-edit these tool-managed fields.

## Issue Lifecycle

```text
active/      -> active, planned, investigating, or in_progress
completed/   -> completed
```

Move an issue to `completed/` only when the requested scope is genuinely done.
If meaningful work remains, keep the issue active or create a follow-up issue
and reference it from the completed issue.

## Delegation Cycle

The canonical handoff is author -> implementer -> reviewer:

1. The author writes an implementation-ready issue or proposal and stops.
2. The issue remains unassigned in the open implement pool unless a specific
   implementer is required.
3. An idle implementer claims the issue with `claim_next_task` or
   `issuekit claim`, does the work, and submits it with `submit_for_review` or
   `issuekit submit-review`.
4. The review handoff omits `reviewer` when `default_reviewer = "auto"` so the
   issue enters the open review pool.
5. A reviewer approves the issue to complete it, or requests changes and sends
   it back to implementation.
6. For requested changes, the implementer handles the review feedback and
   submits the issue for review again.

This is a pull model. Authors publish work to the open implement pool,
implementers pull work when idle, and reviewers pull review work when idle. No
central orchestrator is required for the normal cycle.

An orchestrator or author may also drive a configured external implementer
directly:

```bash
issuekit implement <id> --agent <agent> --timeout-sec <n>
```

That command claims or operates on the assigned issue, runs the configured
agent, and submits the completed work for review. Use it when you need to push a
specific issue through a named external agent instead of waiting for an idle
implementer to pull from the queue.

Separation-of-duties rules:

- The author role and implementer role must be different sessions. Explicit
  author self-assignment is rejected; a same agent name claiming through the
  open implement pool represents a distinct operator/session.
- The implementer and reviewer must be different sessions. Explicit
  implementer self-review is rejected; the open review pool may route to a
  distinct same-named session.
- The author may also be the reviewer when a different implementer did the
  work.

## Creating A New Issue

Use `issuekit author` to create a stamped issue and stop without claiming it:

```bash
issuekit author --title "Short issue title" --body-file plan.md --agent codex
```

Pass `--assign <agent>` only for an explicit handoff to one implementer. Without
`--assign`, the issue remains in the open implement pool for any configured
agent to claim later.

Manual creation is still allowed when needed:

1. Run `issuekit info`.
2. Use the reported next issue id.
3. Create `docs/issues/active/NNN_slug.md` with a snake_case slug.
4. Fill in the template below in English ASCII. Frontmatter is required.
5. Run `issuekit generate-indexes`.
6. Run `issuekit validate`.

The `NNN` id must be unique across both `active/` and `completed/`.

## Completing An Issue

Issues normally reach `completed/` through the reviewer's `approve` step after
`submit_for_review`. The reviewer MCP flow uses `approve`; the CLI completion
spelling is `issuekit complete <id>` until the `issuekit approve` alias is
available. To close an issue directly without review, for example abandoned or
trivial issues, use:

```bash
issuekit complete <id> --force --summary "..." --verification "..."
```

This updates frontmatter, appends completion notes, moves the file from
`active/` to `completed/`, regenerates indexes, and validates the tracker.

## Issue Template

```markdown
---
id: N
status: active
priority: medium
created: YYYY-MM-DD
completed:
title: Short issue title
---

# Issue #N: Short issue title

## Problem

Describe the current problem.

## Proposed Solution

Describe the proposed solution.

## Impact

- Affected file or module
- Affected behavior

## Implementation Plan

1. Step
2. Step

## Test Plan

- Verification command or manual check

## Related Resources

- Related file or issue
```

## Validation Rules

`issuekit validate` checks:

- every issue filename starts with a numeric id
- issue ids are unique across `active/` and `completed/`
- generated index files exist and match current issue files
- generated files contain the generated-file marker
- frontmatter ids match filenames
- frontmatter status and priority use allowed ASCII values
- frontmatter has `created` and `title`
- completed issues use `status: completed`; active issues do not
- issue files contain only ASCII characters
- issue files are valid UTF-8

`issuekit check-encoding` checks tracked files for BOM, mojibake patterns, and
CRLF line endings. `issuekit validate` owns issue tracker structure and issue
file decodability.
