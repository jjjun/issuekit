---
id: 11
status: completed
priority: high
created: 2026-06-01
completed: 2026-06-01
stage: done
title: Add agent-handoff workflow transitions and CLI commands
---



# Issue #11: Add agent-handoff workflow transitions and CLI commands

## Problem

Issue #10 adds the `assignee`/`stage` model but no behavior. There is no way to
atomically pick up the next issue for an agent, hand it off to the other agent,
or send it back for changes. Without these transitions the shared-queue
workflow (codex implements, claude reviews) cannot run, and concurrent agents
could both claim the same issue.

## Proposed Solution

Add a pure-Python `issuekit/workflow.py` with the queue transition functions,
built on the `write_issue_atomic` primitive from issue #10, plus thin CLI
subcommands that expose them. Mutually exclude concurrent transitions with a
single directory-scoped lock created via `O_EXCL`, so candidate selection and
the write happen inside the lock and two concurrent `claim`s cannot grab the
same issue. The lock provides mutual exclusion; `write_issue_atomic` provides
crash safety. The two roles are distinct, so both are used together. Keep
`workflow.py` dependency-free; the CLI and the later MCP server (issue #12) both
call the same functions.

Note: renaming the issue file itself as a claim token is deliberately avoided.
The `NNN_slug.md` filename is part of the spec and generated indexes link to
that path, so renaming on claim would churn indexes and break stable links.

## Impact

- New: `issuekit/workflow.py`
- New: `issuekit/commands/claim.py`, `issuekit/commands/handoff.py`
  (`submit-review`, `request-changes`), `issuekit/commands/queue.py`
- Modified: `issuekit/cli.py` (register new subparsers)
- Modified: `.gitignore` (ignore the `active/.issuekit-claim.lock` lock file)
- New: `tests/test_workflow.py`, `tests/test_workflow_cli.py`

## Implementation Plan

1. In `issuekit/workflow.py` add the claim-lock primitive (stdlib only):
   - `claim_lock(active_dir, timeout=10.0)`: a context manager that creates
     `active_dir / ".issuekit-claim.lock"` with
     `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` and writes a small
     JSON payload (`pid`, `ts`). On `FileExistsError`, poll until `timeout`; if
     the existing lock is older than a stale threshold (for example 60s),
     reclaim it (the previous holder crashed). Always `unlink(missing_ok=True)`
     in a `finally`. This serializes all transitions, so no compare-and-set is
     needed inside the lock.
   - The lock file is `.lock`, not `.md`, so `read_issues` (which filters to
     `.md`) and `validate` ignore it. Add it to `.gitignore`.
2. Implement the pure transition functions, each running its read/select/write
   inside `claim_lock` and returning the affected `Issue` (or `None`):
   - `claim_next(issues_dir, assignee, priority=None)`: select the highest-
     priority issue ready for `assignee` (issue `status` in {active,
     in_progress}, stage in {"", "todo", "changes_requested"}, and assignee in
     {"", assignee}; skip `planned`/`investigating`). Tie-break by ascending id.
     Set `status=in_progress`, `assignee=<assignee>`, `stage=implementing` and
     write with `write_issue_atomic`. Because selection and write share the
     lock, two concurrent claims cannot pick the same issue.
   - `submit_for_review(issues_dir, id, summary, branch=None, commit=None)`:
     require the current assignee to match the submitting agent; set
     `assignee=claude`, `stage=review`; append a "## Handoff" note with summary,
     branch, commit.
   - `request_changes(issues_dir, id, notes)`: set `assignee=codex`,
     `stage=changes_requested`; append a "## Review Feedback" note.
   - `find_for(issues_dir, assignee, stage=None)`: list matching active issues
     (read-only; no lock required).
3. Validate inputs before writing: `assignee`/`stage` arguments go through the
   token-shape check and the configured allowed set from issue #10; free-text
   `summary`/`notes`/`branch`/`commit` go through `has_non_ascii`. Reject
   invalid input rather than writing it into frontmatter or the body.
4. Reuse `read_issues` / `parse_issue_frontmatter` / `format_issue_frontmatter`
   from core; never hand-edit frontmatter strings.
5. Add CLI subcommands in `issuekit/cli.py`:
   - `issuekit claim --assignee codex [--priority high]`
   - `issuekit submit-review <id> --summary ... [--branch ...] [--commit ...]`
   - `issuekit request-changes <id> --notes ...`
   - `issuekit queue --assignee claude [--stage review]`
   Each prints a compact, machine-readable summary (id, file, new
   assignee/stage). ASCII-only inputs, consistent with existing commands.
6. Approval/closing reuses the existing `issuekit complete` command. To make it
   callable from `workflow.py` and the issue #12 MCP server, extract the body of
   `complete.run` into a pure `complete_issue(issues_dir, id, summary,
   verification)` function and keep the argparse wrapper thin. Also migrate that
   completion write onto `write_issue_atomic` (it currently uses `write_text`),
   so all transitions share one write path. Do not duplicate completion logic.
   `complete_issue` sets the terminal workflow state: `stage=done` and clears
   `assignee` (a finished issue has no queue owner; the audit trail lives in the
   handoff/completion notes). This is the only transition that sets `done`. The
   completed frontmatter must therefore include the `stage` field; update the
   data dict that `complete` builds so `stage` is no longer dropped.
7. All writes go through `write_issue_atomic`; no CRLF, no BOM.

## Test Plan

- `uv run pytest tests/test_workflow.py tests/test_workflow_cli.py`
- `claim_next` picks highest priority then lowest id; sets in_progress/codex/
  implementing. It skips `planned`/`investigating` issues.
- Lock mutual exclusion: with the lock file already present (held), `claim_next`
  blocks until `timeout` and then raises rather than double-claiming.
- Stale-lock recovery: a lock file with an old `ts` is reclaimed and the claim
  succeeds.
- `submit_for_review` flips ownership to claude/review and appends the handoff
  note with branch/commit; it rejects a caller whose assignee does not match.
- `request_changes` flips back to codex/changes_requested and appends feedback;
  a subsequent `claim_next("codex")` re-picks it.
- `complete_issue` sets `stage=done`, clears `assignee`, and the completed file
  carries `stage: done` in its frontmatter.
- Input validation: an `assignee`/`stage` that fails the token-shape check, and
  a non-ASCII `summary`/`notes`, are rejected before any write.
- `queue` lists only matching issues.
- The `.issuekit-claim.lock` file is not picked up by `read_issues` or reported
  by `validate`.
- Byte-level: no BOM/CRLF in any rewritten file. Run full `uv run pytest`.

## Related Resources

- Issue #10 (`assignee`/`stage` model, `write_issue_atomic`) - required
- `issuekit/cli.py`, `issuekit/commands/complete.py` (style reference)
- Issue #12 (MCP server wraps these same functions)

**Completed**: 2026-06-01

## Completion Notes

- Added handoff workflow transitions, CLI commands, locking, and complete_issue reuse.
- Verification: `uv run pytest`
