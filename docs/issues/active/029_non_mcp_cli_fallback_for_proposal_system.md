---
id: 29
status: in_progress
priority: medium
created: 2026-06-04
completed: 
assignee: codex
stage: review
implementer: codex
title: Provide a non-MCP CLI fallback with parity for the proposal system
---

# Issue #29: Provide a non-MCP CLI fallback with parity for the proposal system

## Problem

The proposal system is exposed as MCP tools (`propose`, `list_incoming`,
`adopt_proposal` in `issuekit/mcp/server.py`). In practice the MCP path is
unstable under some agent harnesses: a cross-repo write performed inside the MCP
server can be safety-gated and hang, so an operator cannot reliably send or
triage proposals through MCP.

The CLI already has `propose` / `incoming` / `adopt` / `discard`, and the MCP
tools are thin wrappers over the same core functions (`build_proposal`,
`write_proposal`, `adopt_proposal`, `list_incoming`, `proposal_dict`). But the
CLI is not a true drop-in fallback because of three parity gaps:

- `propose` accepts only `--body-file`, not an inline `--body`, even though
  `build_proposal` (`issuekit/commands/propose.py`) already takes a `body`
  argument; `run_propose` hardcodes `body=None`.
- `propose` has no `--json`; it prints only `Wrote proposal: <path>`, so callers
  cannot get the structured payload the MCP `propose` tool returns
  (`{**proposal_dict, "path"}`).
- `adopt` has no `--json`; it prints only `Adopted proposal as: <path>`, while
  the MCP `adopt_proposal` returns the adopted issue dict (with body).

`incoming --json` is already at parity with `list_incoming`.

## Proposed Solution

Close the gaps and guarantee that the CLI emits byte-identical JSON to the MCP
tools by sharing one serializer, so the CLI is a safe fallback when MCP is
unstable. Keep the MCP tools unchanged in behavior.

- Extract the issue serializer into a shared, public helper
  `issue_dict(issue, *, include_body=False)` in `issuekit/core.py` (next to the
  `Issue` dataclass), mirroring how `proposal_dict` is shared from
  `issuekit/proposals.py`. The MCP server uses this helper so the two paths
  cannot drift.
- `propose` CLI: add `--body` (inline) and `--json`. Wire `body=args.body` into
  `build_proposal` (its `_proposal_body` already prefers `body` over
  `body_file`). On `--json`, print
  `json.dumps({**proposal_dict(proposal), "path": path.as_posix()})`, the exact
  MCP `propose` shape. Without `--json`, keep the current human line.
- `adopt` CLI: add `--json`. After adopting and regenerating indexes, read the
  adopted issue back and print `json.dumps(issue_dict(issue, include_body=True))`,
  matching MCP `adopt_proposal`. Without `--json`, keep the current human line.
- Document the fallback as first-class: an MCP-to-CLI mapping in the handoff
  protocol text (`issuekit/protocol.py`) and `docs/issues/README.md`, plus a
  short note in `CLAUDE.md`.

## Impact

- Modified: `issuekit/core.py` (new `issue_dict` helper).
- Modified: `issuekit/mcp/server.py` (use shared `issue_dict`; no behavior change).
- Modified: `issuekit/cli.py` (add `--body`/`--json` to `propose`, `--json` to
  `adopt`).
- Modified: `issuekit/commands/propose.py` (inline body + JSON output in
  `run_propose`; JSON output in `run_adopt`).
- Modified: `issuekit/protocol.py`, `docs/issues/README.md`, `CLAUDE.md` (mapping
  and fallback rule).
- Modified: tests (`tests/test_proposals.py`, `tests/test_mcp_server.py`).
- No change to existing JSON keys, exit codes, or non-`--json` output. Out of
  scope: applying the same `--json` pattern to the workflow tools
  (`claim`/`submit-review`/`request-changes`/`approve`/`queue`).

## Implementation Plan

1. Add `issue_dict(issue, *, include_body=False)` to `issuekit/core.py`; have
   `issuekit/mcp/server.py` import and delegate to it from `_issue_dict`.
2. In `issuekit/cli.py`, add `--body` and `--json` to the `propose` subparser and
   `--json` to the `adopt` subparser.
3. In `issuekit/commands/propose.py`, pass `body=args.body` in `run_propose` and
   emit the MCP-shaped JSON under `--json`; in `run_adopt`, read the adopted issue
   via `read_all_issues` (match `file_path == path`) and emit
   `issue_dict(..., include_body=True)` under `--json`.
4. Add the MCP-to-CLI mapping and fallback rule to `issuekit/protocol.py`,
   `docs/issues/README.md`, and `CLAUDE.md`. Keep all text ASCII.

## Test Plan

- `tests/test_proposals.py`: CLI `propose --to <ref> --title T --body B --json`
  writes the proposal and prints the structured payload (keys `file`, `origin`,
  `to`, `reply_to`, `created`, `title`, `path`); `propose` without `--json` still
  prints `Wrote proposal:`; `adopt <file> --json` prints the issue payload
  (`id`, `title`, `status`, `assignee`, `stage`, `implementer`, `file`, `body`).
- `tests/test_mcp_server.py`: for the same inputs, CLI `propose --json` output
  equals the MCP `propose` result, `incoming --json` equals `list_incoming`, and
  `adopt --json` keys match `adopt_proposal` (build the server via
  `create_server`).
- Run `uv run pytest` (and `uv run --extra mcp pytest tests/test_mcp_server.py`),
  `uv run issuekit validate`, and `uv run issuekit check-encoding`.

## Related Resources

- `issuekit/mcp/server.py` (`propose` L159, `list_incoming` L180, `adopt_proposal`
  L185, `_issue_dict` L224)
- `issuekit/commands/propose.py` (`run_propose`, `run_adopt`, `build_proposal`,
  `_proposal_body`)
- `issuekit/proposals.py` (`proposal_dict` L137)
- `issuekit/core.py` (`Issue` dataclass), `issuekit/cli.py` (subparsers)
- `tests/test_mcp_server.py` (`_call`, `test_proposal_tools_send_list_and_adopt`)
- `docs/issues/README.md` ("Cross-Project Proposals")

## Handoff

- Summary: Added CLI proposal fallback parity with inline body support, JSON outputs matching MCP proposal and adopt tools, shared issue serialization, docs, and regression tests.
- Branch: `main`
- Commit: `54cdfa8`
