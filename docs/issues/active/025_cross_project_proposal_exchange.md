---
id: 25
status: in_progress
priority: medium
created: 2026-06-03
completed:
assignee: codex
stage: implementing
implementer: codex
title: Cross-project proposal exchange with reply loop
---

# Issue #25: Cross-project proposal exchange with reply loop

## Problem

Related repositories (for example a library and a consumer) often need to send
change suggestions to each other. Today issuekit is strictly single-repo: issue
ids are per-repo integers, `validate`/`generate-indexes`/`claim_next_task` only
scan `active/` and `completed/`, and `config` (assignees, stages) is per-repo.
There is no supported way for repo A to hand a suggestion to repo B, for B to
triage and adopt it as a local issue, or for B to return its implementation
result back to A after the work is done.

We want a lightweight, file-based exchange that does NOT disturb the existing
tracker or the two-agent handoff:

1. A writes a proposal targeted at B.
2. B sees the incoming proposal, then either discards it or adopts it as a
   local issue in B's own format.
3. B implements the adopted issue, and at completion returns a proposal to A
   describing what was implemented.
4. A reviews the returned proposal and either adopts it as a local issue or
   sends another proposal back.

The hard part to avoid is distributed state sync. A proposal must carry
*content*, not *status*: B's return proposal is a brand new inbound suggestion
to A, never a callback that mutates A's original proposal. This keeps the
exchange fire-and-forget and avoids a cross-repo state machine.

## Proposed Solution

Add a directional, file-based proposal channel plus a small local registry of
related repos. A proposal is a single Markdown file dropped into the *target*
repo, kept outside `active/`/`completed/` so it is invisible to validate,
indexes, and the claim queue until a human or agent adopts it.

Key design choices (decided in design review):

- Proposals live in `docs/issues/incoming/` in the target repo. `read_issues`
  only scans `active/` and `completed/` (`issuekit/core.py` L136), so this dir
  is ignored by `validate` and `generate-indexes` automatically. No core
  scanning changes are needed.
- Proposals carry only provenance + free text, never workflow fields
  (`assignee`/`stage`/`priority`). This prevents A's vocabulary from leaking
  into B, where those tokens may be undefined.
- Cross-repo identity uses a stable origin string `<ref>#<id>@<commit>`, since
  per-repo integer ids collide. The optional `reply_to` field links a return
  proposal back to the original, forming a greppable thread.
- The refs registry maps a short name to an absolute local path. Absolute paths
  are not portable, so the registry lives in a machine-local, gitignored file
  (`issuekit.local.toml`), separate from committed config.
- Proposal files themselves ARE committed in the target repo (shared artifact /
  audit trail); only the path registry is local.

### Proposal file format

```markdown
---
origin: mine-py#42@<commit>
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

All proposal text is English ASCII (consistent with docs/issues rules).
`reply_to` is empty for an initial proposal and set to the origin of the issue
being answered for a return proposal.

## Impact

- New: `issuekit/refs.py` (load/save the local refs registry, resolve a ref name
  to a target repo path and its issues dir)
- New: `issuekit/proposals.py` (proposal frontmatter model, write to a target
  `incoming/`, list local `incoming/`, adopt into a new `active/` issue,
  discard)
- New: `issuekit/commands/propose.py` (`propose`, `incoming`, `adopt`,
  `discard`, `add-ref`, `list-refs` command handlers)
- Modified: `issuekit/cli.py` (register the new subcommands)
- Modified: `issuekit/mcp/server.py` (expose `propose`, `list_incoming`,
  `adopt_proposal` tools for agent ergonomics)
- Modified: `issuekit/protocol.py` (one short step describing how to send/triage
  cross-project proposals; ASCII)
- Modified: `issuekit/commands/init.py` (scaffold `docs/issues/incoming/`
  placeholder and add `issuekit.local.toml` to `.gitignore`)
- Modified: `README.md`, `docs/issues/README.md` (document the channel and the
  proposal format)
- New/Modified tests: `tests/test_refs.py`, `tests/test_proposals.py`,
  `tests/test_cli.py`, `tests/test_mcp_server.py`

## Implementation Plan

Land this in focused commits; stages 1-4 are the one-way channel, stage 5 adds
the reply loop, stage 6 wires MCP/protocol.

1. Refs registry (`issuekit/refs.py`):
   - Read/write `issuekit.local.toml` at repo root with a `[refs]` table mapping
     `name = "C:/abs/path/to/repo"`.
   - `add_ref(name, path)` validates the path exists and stores it; `list_refs()`
     returns the map; `resolve_ref(name)` returns the repo path and its issues
     dir (load the target's own `IssuekitConfig` to find its `issues_dir`).
   - Reject unknown names with a clear error.

2. Proposal IO (`issuekit/proposals.py`):
   - `Proposal` dataclass: origin, to, reply_to, created, title, body.
   - `write_proposal(target_issues_dir, proposal)` -> writes
     `incoming/<source>__<sourceid>__<slug>.md`, UTF-8 no BOM, LF, ASCII-only;
     create `incoming/` if missing. Refuse to overwrite an existing file with
     the same origin (idempotent send).
   - `list_incoming(issues_dir)` -> parse all `incoming/*.md` into `Proposal`s.
   - Frontmatter parsing reuses the existing helpers in `issuekit/core.py`.

3. `propose` / `add-ref` / `list-refs` commands (`issuekit/commands/propose.py`,
   `issuekit/cli.py`):
   - `issuekit add-ref <name> --path <path>` and `issuekit list-refs`.
   - `issuekit propose --to <name> --title "..." [--body-file <f>] [--from-issue <id>]`
     resolves the ref, captures the current source `<ref>#<id>@<commit>` origin
     (ref = this repo's own ref name or directory name; commit from `git rev-parse
     --short HEAD`), and writes the proposal into the target's `incoming/`.
   - `--from-issue <id>` pre-fills origin id and title from a local issue.

4. `incoming` / `adopt` / `discard` commands:
   - `issuekit incoming [--json]` lists proposals waiting in this repo.
   - `issuekit adopt <proposal-file> [--priority medium]` allocates the next
     local id (same logic as `info`), writes
     `active/NNN_<slug>.md` using the standard issue template, copies the
     proposal body into Problem/Proposed Solution context, records the proposal
     `origin` in the new issue's frontmatter as `origin:` (an unknown key that
     `validate` tolerates) and under `## Related Resources`, then moves the
     consumed proposal to `incoming/adopted/` (or deletes it). Run
     `generate-indexes` after.
   - `issuekit discard <proposal-file>` moves it to `incoming/discarded/` (or
     deletes it). Adoption is a human/agent judgment call; these commands only
     scaffold and record provenance, they do not auto-translate content.

5. Reply loop:
   - `issuekit propose --reply <issue-id> --title "..."` reads the local issue's
     recorded `origin:` to derive the destination ref and sets `reply_to` to that
     origin, and sets the new proposal's own `origin` to `<this-ref>#<issue-id>@<commit>`.
     The agent writes the body (what was implemented). This is the
     work-reducing path: destination and threading are auto-filled from data
     stored at adopt time.
   - A return proposal lands in A's `incoming/` like any other; the presence of
     `reply_to` is the triage signal that "this answers something I sent."

6. MCP + protocol:
   - Add MCP tools `propose`, `list_incoming`, `adopt_proposal` mirroring the CLI
     (read root via `_context`).
   - Add a short ASCII section to `issuekit/protocol.py`: before claiming, check
     `incoming` and triage; on completing an adopted issue, optionally
     `propose --reply` back to the origin. Propagates via `get_protocol`.

### Out of scope (explicitly NOT built)

- No status sync: a return proposal never mutates or closes the original
  proposal on the other side.
- No convergence/loop detection: repeated reject-and-re-propose rounds are
  allowed; humans stop the loop.
- No acknowledgement requirement: sending is fire-and-forget.
- No automatic content translation: adoption is a judgment step.

## Test Plan

- `uv run pytest tests/test_refs.py tests/test_proposals.py tests/test_cli.py
  tests/test_mcp_server.py`
- Refs: `add-ref` stores and `list-refs`/`resolve_ref` return the path; unknown
  ref name raises; missing target path raises.
- Propose (one-way): `propose --to B` writes one ASCII, LF, BOM-free file under
  B's `incoming/` with correct `origin`/`to`/`title`; re-sending the same origin
  does not duplicate or overwrite.
- Isolation: after a proposal is written under `incoming/`, `issuekit validate`
  and `generate-indexes` in the target repo are unaffected, and
  `claim_next_task` does not surface it.
- Adopt: `adopt` allocates the next id, creates a valid `active/NNN_*.md` that
  passes `validate`, records `origin:` in frontmatter and Related Resources, and
  moves/removes the source proposal. `discard` removes it without creating an
  issue.
- Reply: after adopting B#NN (origin A#42), `propose --reply NN` produces a
  proposal addressed to A with `reply_to: A#42` and origin `B#NN@<commit>`.
- Triage signal: `incoming` distinguishes proposals with and without `reply_to`.
- MCP: `propose`, `list_incoming`, `adopt_proposal` behave like their CLI
  counterparts; `get_protocol` text stays ASCII and matches the CLI.
- Run full `uv run pytest`, `uv run issuekit validate`, and
  `uv run issuekit check-encoding`.

## Related Resources

- `issuekit/core.py` (`read_issues` L136 scans only active/completed; frontmatter
  helpers `parse_issue_frontmatter` L55)
- `issuekit/config.py` (per-repo config; add a separate local refs loader)
- `issuekit/workflow.py` (`claim_next` L79 reads active/ only; unaffected)
- `issuekit/commands/validate.py` (tolerates unknown frontmatter keys like
  `origin:`)
- `issuekit/cli.py`, `issuekit/mcp/server.py`, `issuekit/protocol.py`
- `docs/issues/README.md` (issue spec to extend with the proposal format)

## Handoff

- Summary: Implemented file-based cross-project proposals with local refs, CLI and MCP tools, docs, init scaffolding, and tests.
- Branch: `main`
- Commit: `ff83164`

## Review Feedback

- Blocker: reply loop is broken in the real flow. adopt stores origin in the issue frontmatter, but claim_next/_write_active_issue rebuilds frontmatter from a fixed key set and drops origin (verified: origin present after adopt, gone after claim). So propose --reply fails with 'has no origin field' after the issue is claimed/implemented, which is the normal path (adopt -> claim -> implement -> reply). Existing test_reply_proposal_uses_adopted_issue_origin passes only because it never claims the issue. Fix options: (a) preserve passthrough frontmatter keys like origin in _write_active_issue and the complete path; or (b) fall back to the Related Resources Origin line in build_proposal --reply. Add a regression test covering adopt -> claim -> submit/complete -> reply. Minor 1: reply --to is auto-derived from origin_destination, which equals the sender repo directory name (default_repo_ref); the receiver must register a ref whose name exactly matches that, or reply resolution fails. Document this or allow an explicit --to override on reply. Minor 2: idempotent-send dedup keys on origin which includes @commit, so re-sending after a new commit creates a duplicate file; note the limitation.
