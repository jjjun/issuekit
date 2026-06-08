---
id: 58
status: active
priority: high
created: 2026-06-09
completed: 
stage: todo
author: claude
title: Harden proposal file path resolution against directory traversal
---

# Issue #58: Harden proposal file path resolution against directory traversal

## Problem

`issuekit/proposals.py` `_resolve_proposal_path` resolves a caller-supplied
`proposal_file` without constraining it to the incoming directory:

```python
def _resolve_proposal_path(issues_dir, proposal_file):
    path = Path(proposal_file)
    if path.is_absolute():
        return path
    incoming = issues_dir / "incoming"
    if (incoming / path).exists():
        return incoming / path
    return Path.cwd() / path
```

This path flows from `adopt_proposal` and `discard_proposal`, which are exposed
both on the CLI (`issuekit adopt`/`discard`) and as the MCP tool
`adopt_proposal(proposal_file=...)` driven by an external agent. Consequences:

- An absolute path is returned as-is, so `adopt` will read any file on disk
  (e.g. a secret) and copy its contents into a new active issue, and `discard`
  will `shutil.move` an arbitrary file into `incoming/discarded`.
- A relative path with `..` segments (e.g. `../../something.md`) escapes the
  incoming directory: the `(incoming / path).exists()` check resolves `..`, so a
  traversal target that happens to exist is accepted; otherwise it falls through
  to `Path.cwd() / path`, again allowing traversal.

For a local dev tool the blast radius is limited, but `adopt_proposal` is
agent-callable over MCP, so an attacker-influenced or confused agent can be
steered to exfiltrate file contents into the tracker or move files out of place.

## Proposed Solution

Constrain proposal resolution to the `incoming/` directory and reject anything
that escapes it:

1. Accept only a bare file name or a path that, once resolved, is contained
   within `issues_dir/incoming/`. Reject absolute paths and any path whose
   resolved location is outside `incoming/` with a clear `ProposalError`.
2. Validate that the resolved target is an existing regular file inside
   `incoming/` before reading or moving it.
3. Keep the ergonomic behavior of passing just the file name (the common case
   from `issuekit incoming`).

## Impact

- `issuekit/proposals.py` `_resolve_proposal_path` (and callers
  `adopt_proposal`, `discard_proposal`).
- `issuekit/commands/propose.py` and `issuekit/mcp/server.py` surface the new
  error message.
- Tests in `tests/test_proposals.py` for traversal/absolute-path rejection.

## Implementation Plan

1. Rewrite `_resolve_proposal_path` to resolve against `incoming/` and verify
   containment using a resolved-path prefix check; raise `ProposalError` on
   escape, absolute path, or non-file target.
2. Preserve the bare-file-name happy path.
3. Add tests: bare name (ok), `..` traversal (rejected), absolute path
   (rejected), nonexistent file (clear error), non-file path (rejected).

## Test Plan

- `uv run pytest tests/test_proposals.py tests/test_mcp_server.py`
- `uv run pytest`
- `uv run issuekit incoming` then `issuekit adopt <name>` smoke check.

## Related Resources

- `issuekit/proposals.py` `_resolve_proposal_path`, `adopt_proposal`,
  `discard_proposal`
- MCP tool `adopt_proposal` in `issuekit/mcp/server.py`
- Note: subprocess usage in the codebase already uses list-form args (no
  `shell=True`) and redirects stdin to DEVNULL, so this proposal-path gap is the
  main input-validation issue found in this pass.
