---
id: 42
status: completed
priority: high
created: 2026-06-08
completed: 2026-06-08
stage: done
title: Record issue author and add an agent-agnostic author command
---

# Issue #42: Record issue author and add an agent-agnostic author command

## Problem

The handoff already records who implemented (`implementer`) and who reviewed
(`reviewer`), but it does not record who authored an issue. The author role
exists only as protocol text (#34): `issuekit protocol --role author` tells an
agent to write the issue and STOP, but nothing in the data layer captures the
author. As a result the "author != implementer" discipline from #34 can only be
requested by prose, not enforced (see #43), and authoring is still done by
hand-writing a Markdown file under `docs/issues/active/` instead of a single
repeatable command.

To let any registered agent (claude, codex, kimi) be an author the same way --
not just claude -- the author must become a first-class, recorded role with a
command, mirroring how #32/#38 made implementer and reviewer agent-agnostic.

## Proposed Solution

Add an `author` frontmatter field and an `issuekit author` command that stamps
it while creating a template-conformant issue.

1. Add an optional `author` field to the issue model and frontmatter, alongside
   the existing tool-managed `assignee` / `stage` / `implementer` fields.
2. Add `issuekit author` that:
   - resolves the next id (same logic as `issuekit info`),
   - writes `docs/issues/active/NNN_slug.md` from the standard template with
     `status: active`, an unstarted stage (`todo` or empty), no assignee, and
     `author: <agent>`,
   - runs the equivalent of `generate-indexes` and `validate`,
   - STOPS without claiming or implementing (enforces the #34 handoff).
3. Leave the implementer unassigned by default so the issue sits in an open
   implement pool that any agent can `claim_next_task`; accept an optional
   `--assign <implementer>` override for explicit hand-off.

## Impact

- `issuekit/core.py`: add `author` to the issue model and frontmatter parsing.
- `issuekit/commands/author.py` (new): the command implementation.
- `issuekit/cli.py`: register the `author` subcommand (`--title`, `--body` /
  `--body-file`, `--priority`, `--agent`, optional `--assign`).
- `issuekit/workflow.py`: a helper to write/stamp the author field if issue
  creation is centralized there.
- `docs/issues/README.md`: document the `author` field in Issue Metadata.
- `tests/`: author command creates a valid active issue, stamps `author`,
  leaves no assignee by default, does not claim or implement.

## Implementation Plan

1. Extend the issue model + frontmatter (read and write) with `author`.
2. Implement `issuekit/commands/author.py` and register it in `issuekit/cli.py`.
3. Generate indexes and validate at the end of a successful author run.
4. Document the `author` field in `docs/issues/README.md` Issue Metadata.
5. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_author_command.py`
- Manual: `issuekit author --title "..." --body-file plan.md --agent codex`
  creates an active issue with `author: codex`, no assignee, stage unstarted,
  and makes no commit.
- `uv run issuekit validate`

## Related Resources

- Issue #34 (author protocol; this adds the data + command behind it)
- Issue #38 (config-driven agent registry that makes agents interchangeable)
- Issue #43 (separation-of-duties guard that consumes the `author` field)
- `issuekit/core.py`, `issuekit/cli.py`, `issuekit/workflow.py`
- `docs/issues/README.md` (Issue Metadata)

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-08

## Completion Notes

- Approved by codex.
- Verification: `Reviewed by claude (distinct from implementer codex; routed via open pool). author field threaded through core.py (Issue model, issue_dict, format_issue_frontmatter omit-if-empty, read_issues), workflow.py active rewrites, complete.py completion, and validate.py token/membership checks. New issuekit author command creates a template-conformant active issue under claim_lock with author stamped, stage todo, implementer empty, assignee empty by default and optional --assign override; runs generate-indexes and validate, then STOPS without claiming/implementing per the #34 handoff. Verified: uv run pytest (233 passed, 21 skipped), uv run issuekit validate (44 files, 0 warnings), uv run issuekit check-encoding clean. Tests cover the open-pool default and the --assign path.`
