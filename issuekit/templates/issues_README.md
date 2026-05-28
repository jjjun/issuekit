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
  indexes/                   # generated issue indexes; never edit by hand
    active.md
    completed-recent.md
    completed-001-099.md
    ...
```

`README.md` does not contain the full issue table. Generated indexes are split
so the system stays usable as completed issues grow.

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
