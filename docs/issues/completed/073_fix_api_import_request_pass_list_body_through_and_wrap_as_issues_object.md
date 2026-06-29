---
id: 73
status: completed
priority: high
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: Fix API import request: pass list body through and wrap as issues object
---

# Issue #73: Fix API import request: pass list body through and wrap as issues object

## Problem

`issuekit migrate-to-api` fails against the real server with:

```
Migration failed: dictionary update sequence element #0 has length 14; 2 is required
```

No request reaches the server; the error is raised client-side while building the
request. Two bugs in issuekit/client.py, both in the API import path, which was
never exercised end-to-end because FakeIssuekitClient.import_issues bypasses the
HTTP layer:

1. `_request` coerces the JSON body with `json=dict(json) if json is not None
   else None` (two call sites: the initial send and the 401-retry send). This is
   fine for a Mapping body, but `import_issues` passes a LIST. `dict([...])` then
   tries to unpack each list element (a 14-key issue dict) as a (key, value)
   pair, raising "dictionary update sequence element #0 has length 14; 2 is
   required".

2. `import_issues` sends a BARE LIST as the body, but the server's
   `IssueImportRequest` (mine-py/src/domains/issues/schemas/issue.py) requires an
   object `{"issues": [IssueImportItem, ...]}` and is `extra="forbid"`. Even if
   bug 1 were fixed, a bare list would be rejected with HTTP 422.

## Proposed Solution

In issuekit/client.py:

1. Stop coercing the request body to a dict in `_request`. httpx serializes both
   lists and dicts as JSON, so pass the body through unchanged. Replace both
   `json=dict(json) if json is not None else None` occurrences with `json=json`
   (the body is already a plain dict/list built by callers). Update the `json`
   parameter type hint to accept a list as well as a Mapping.

2. In `import_issues`, wrap the items in the schema object the server expects:
   send `json={"issues": items}` where `items` is the list of issue dicts. Keep
   accepting either a list or a single mapping for `issues` as today, but always
   POST the `{"issues": [...]}` shape. The endpoint responds with a list of
   issues; keep returning the parsed list.

Do not change other call sites' behavior: `create_issue` currently passes
`json=dict(issue)` and the transition methods pass plain dicts; those remain
valid dict bodies and must keep working.

## Impact

- issuekit/client.py (`_request`, `import_issues`). No server change; this aligns
  the client with the existing IssueImportRequest contract.
- Unblocks `issuekit migrate-to-api`.

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- Add tests in tests/test_client.py using the existing httpx MockTransport
  pattern that capture the outgoing request:
  - `import_issues([...])` POSTs to `/api/issues/{project}/issues/import` with a
    JSON body exactly equal to `{"issues": [...the items...]}` (assert the parsed
    request content), and returns the server's list response.
  - A regression test that a list JSON body is sent through `_request` without
    being coerced to a dict (i.e. importing a multi-key issue dict no longer
    raises the "dictionary update sequence" ValueError).
  - Confirm a normal dict body (e.g. `create_issue` / a transition) still serializes
    correctly.

## Related Resources

- issuekit/client.py (`_request`, `import_issues`, added in #65).
- issuekit/commands/migrate_to_api.py (caller).
- mine-py/src/domains/issues/schemas/issue.py (`IssueImportRequest`,
  `IssueImportItem`), routes `import_issues` (response is list[IssueResponse]).
- Found during the first real migration run (epic #64 cutover).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (337 passed, 25 skipped via uv run python -m pytest; +3 from prior, no test loss); issuekit check-encoding clean. Reviewed: the two import-path bugs are fixed. (1) _request no longer coerces the body with dict(json) at either the initial send or the 401-retry send; it now passes json=json straight to httpx (type hint widened to JsonBody = Mapping | Sequence[Mapping]), so a list body no longer raises the 'dictionary update sequence element #0 has length 14' ValueError. (2) import_issues now posts json={"issues": items} (normalizing a single mapping to a one-element list), matching the server's IssueImportRequest (extra=forbid) instead of a bare list. Other call sites (create_issue, transitions) still pass plain dict bodies and are unaffected. New tests assert the import request URL path and that request content equals {"issues": items}, plus a regression that a list body passes through _request returning the server list. Scope limited to issuekit/client.py and tests/test_client.py. This unblocks issuekit migrate-to-api.`
