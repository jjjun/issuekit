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

Prefer the completion command when closing an active issue:

```bash
issuekit complete <id> --summary "Completed scope." --verification "issuekit validate"
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

## Cross-Project Proposals

Related repositories can send suggestions as Markdown files in
`docs/issues/incoming/`. These proposal files are outside the issue lifecycle
until someone adopts them, so tracker validation, generated indexes, and claim
commands ignore them.

Machine-local related repo paths live in gitignored `issuekit.local.toml`:

```toml
[refs]
fast-domain = "C:/abs/path/to/fast-domain"
```

Use `issuekit add-ref <name> --path <repo>` and `issuekit list-refs` to manage
that file.

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

For `issuekit propose --reply <id>`, the destination ref is derived from the
recorded `origin` value unless `--to <name>` is also provided. The derived ref
name is the origin text before `#`, so local refs should use the sender's ref
name when possible.

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
title: Short issue title
---
```

Allowed `status` values: `active`, `planned`, `investigating`, `in_progress`,
`completed`.

Allowed `priority` values: `high`, `medium`, `low`.

## Issue Lifecycle

```text
active/      -> active, planned, investigating, or in_progress
completed/   -> completed
```

Move an issue to `completed/` only when the requested scope is genuinely done.
If meaningful work remains, keep the issue active or create a follow-up issue
and reference it from the completed issue.

## Creating A New Issue

1. Run `issuekit info`.
2. Use the reported next issue id.
3. Create `docs/issues/active/NNN_slug.md` with a snake_case slug.
4. Fill in the template below in English ASCII. Frontmatter is required.
5. Run `issuekit generate-indexes`.
6. Run `issuekit validate`.

The `NNN` id must be unique across both `active/` and `completed/`.

## Completing An Issue

Prefer `issuekit complete <id> --summary "..." --verification "..."`. It updates
frontmatter, appends completion notes, moves the file from `active/` to
`completed/`, regenerates indexes, and validates the tracker.

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

`issuekit check-encoding` checks tracked source files for BOM and mojibake
patterns. `issuekit validate` owns issue tracker structure and issue file
decodability.
