---
id: 10
status: completed
priority: high
created: 2026-06-01
completed: 2026-06-01
stage: done
title: Add agent-handoff workflow model (assignee/stage frontmatter)
---



# Issue #10: Add agent-handoff workflow model (assignee/stage frontmatter)

## Problem

issuekit models an issue only as `status` (active/planned/investigating/
in_progress/completed) plus priority. There is no concept of *who* owns an issue
right now or *what phase of a two-agent workflow* it is in. We want to use the
issue tracker as a shared work queue between two AI agents (codex implements,
claude reviews), where each agent can pick up the issue assigned to it. The
current model cannot express "codex is implementing this" vs "this is waiting
for claude to review".

## Proposed Solution

Add two optional frontmatter fields, `assignee` and `stage`, parsed and
validated by issuekit. Keep the existing `status` set unchanged so all
consuming repos stay valid. `assignee`/`stage` are additive and default to
empty (treated as unassigned / no workflow stage). Also add an atomic file
write helper so later workflow transitions cannot corrupt or double-write an
issue file.

Validation has two independent layers so issuekit stays a generic, multi-repo
tool while still protecting file integrity:

1. Token-shape check (integrity floor, always on): a non-empty `assignee`/
   `stage` must match `^[a-z0-9][a-z0-9_-]{0,31}$`. This rejects newlines,
   colons, whitespace, and control characters. ASCII-only is not sufficient
   here: a newline or `:` is ASCII but, written into a frontmatter line, would
   inject a fake key (for example `assignee: codex\nstatus: completed`), which
   the simple frontmatter parser would read back as a real `status` field. The
   pattern prevents that frontmatter injection.
2. Allowed-set check (semantics, configurable): the value must be in the set
   declared in `[tool.issuekit]`. Defaults match this repo (`codex`/`claude`
   and the handoff stages), but no agent name is hardcoded in `core.py`, so
   other repos can declare their own agents/stages.

This issue covers only the model, parsing, validation, config keys, and the
atomic write primitive. The queue transition logic and CLI come in issue #11;
the MCP server in issue #12; agent registration in issue #13.

## Impact

- Modified: `issuekit/core.py` (Issue dataclass, parsing, frontmatter format,
  token-shape pattern + helper, atomic write helper)
- Modified: `issuekit/config.py` (add `assignees`/`stages` config fields with
  defaults)
- Modified: `issuekit/commands/validate.py` (validate assignee/stage values)
- New: `tests/test_workflow_model.py`

## Implementation Plan

1. In `issuekit/core.py` add the token-shape primitive (no hardcoded agent
   names):
   - `WORKFLOW_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")`
   - `is_valid_workflow_token(value: str) -> bool`: return `True` for the empty
     string ("unset"); otherwise return whether the value matches the pattern.
   This is the integrity floor and is independent of any allowed set.
2. In `issuekit/config.py` add two fields to `IssuekitConfig` with defaults that
   match this repo, loaded from `[tool.issuekit]`:
   - `assignees: tuple[str, ...] = ("codex", "claude")`
   - `stages: tuple[str, ...] = ("todo", "implementing", "review",
     "changes_requested", "done")`
   Coerce the loaded TOML lists to tuples of `str`. Empty string is always an
   implicit member meaning "unset" and is not listed here.
3. Extend the `Issue` dataclass with `assignee: str` and `stage: str`; populate
   them in `read_issues` via `_normalize(metadata.get("assignee"))` /
   `_normalize(metadata.get("stage"))`.
4. Extend `format_issue_frontmatter` to emit `assignee` and `stage` lines, but
   only when non-empty, so existing issues that omit them are byte-identical
   after a rewrite. Keep field order: id, status, priority, created, completed,
   assignee, stage, title.
5. Add `write_issue_atomic(path: Path, content: str) -> None` to `core.py`:
   write to a temp file in the same directory with UTF-8 (no BOM) and LF, then
   `os.replace` onto the target. This is the single write path future
   transitions must use. (Migrating the existing `complete` command onto this
   helper is out of scope for this issue; it is noted in issue #11.)
6. In `issuekit/commands/validate.py`, when frontmatter has a non-empty
   `assignee`/`stage`, emit one error if it fails the token-shape check
   (`is_valid_workflow_token`) and one error if it is not in the configured set
   (`config.assignees` / `config.stages`). `validate.run` already loads the
   config. Mirror the existing status/priority validation style and message
   format.

## Test Plan

- `uv run pytest tests/test_workflow_model.py`
- Parsing: an issue with `assignee: codex` / `stage: implementing` is read back
  with those fields; an issue without them yields empty strings.
- Format round-trip: an issue without assignee/stage re-serializes byte-for-byte
  (no spurious empty lines); one with them serializes in the documented order.
- Token-shape: `is_valid_workflow_token` accepts `""` and `codex`, and rejects a
  value containing a newline, a `:`, a space, or a leading `-`. Specifically
  assert that an injection attempt such as `"codex\nstatus: completed"` is
  rejected so it can never reach a frontmatter line.
- validate (semantics): with default config, `assignee: bob` and `stage: foo`
  each produce one allowed-set error; `assignee: codex` and empty values pass.
- validate (config override): a `[tool.issuekit]` config that declares
  `assignees = ["alice"]` makes `assignee: alice` valid and `assignee: codex`
  invalid, proving no agent name is hardcoded.
- `write_issue_atomic` produces no BOM and no CRLF (byte-level assertion) and
  replaces the target in place.
- Run `uv run pytest` to confirm no regression in existing tests.

## Related Resources

- `issuekit/core.py` (`Issue`, `read_issues`, `format_issue_frontmatter`,
  `VALID_ISSUE_STATUSES`)
- `issuekit/config.py` (`IssuekitConfig`, `load_config`)
- `issuekit/commands/validate.py`
- Issue #11 (workflow transitions + CLI, depends on this)

**Completed**: 2026-06-01

## Completion Notes

- Added workflow frontmatter model, config validation, and atomic issue writes.
- Verification: `uv run pytest`
