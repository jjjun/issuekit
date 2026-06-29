---
id: 74
status: completed
priority: high
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: Fix API collection trailing-slash 307 and follow redirects
---

# Issue #74: Fix API collection trailing-slash 307 and follow redirects

## Problem

Against the real mine-py server, collection-level API calls fail with
`Temporary Redirect` (HTTP 307). `issuekit migrate-to-api` reports
"Migration failed: Temporary Redirect", and the same break affects ordinary
reads/creates (info, queue, ApiStore list, author create).

Root cause: the client builds a trailing slash for the collection root, but the
server route has no trailing slash, so Starlette issues a 307 redirect:

```
GET  /api/issues/issuekit/issues/   -> 307  Location: /api/issues/issuekit/issues
POST /api/issues/issuekit/issues/   -> 307  (create)
```

In issuekit/client.py, `list_issues` and `create_issue` call `_request(..., "/")`,
and `_issue_path("/")` yields `.../issues/` (trailing slash). httpx.Client is
created with the default `follow_redirects=False`, so the 307 surfaces as an
error instead of being followed. Item/sub-resource paths (`/{number}`,
`/{number}/claim`, `/import`, `/claim-next`) are unaffected because they have a
non-empty, non-slash suffix. This was never caught because tests use an
in-process MockTransport that does not enforce route slash semantics.

## Proposed Solution

In issuekit/client.py:

1. Stop emitting a trailing slash for the collection root. Update `_issue_path`
   so an empty or "/" path maps to the base with NO trailing slash:

   ```
   def _issue_path(self, path: str) -> str:
       if path in ("", "/"):
           suffix = ""
       elif path.startswith("/"):
           suffix = path
       else:
           suffix = f"/{path}"
       return f"/api/issues/{self.project}/issues{suffix}"
   ```

   So `list_issues` (GET) and `create_issue` (POST) hit
   `/api/issues/{project}/issues` exactly. Callers may keep passing "/".

2. Defense in depth: construct the httpx client with
   `follow_redirects=True` (both the internally created client and document that
   an injected `http_client` should do likewise). On a 307/308 httpx preserves
   the method and body, and keeps the Authorization header on same-origin
   redirects, so any residual slash mismatch still succeeds instead of erroring.

Keep `_url` (used for `/auth/login`, `/auth/logout`) unchanged.

## Impact

- issuekit/client.py (`_issue_path`, httpx.Client construction). No server change.
- Unblocks `issuekit migrate-to-api` (the list/verify step) and fixes all
  API-mode reads (info/queue/ApiStore) and author create against the real server.

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- Add tests in tests/test_client.py with the existing MockTransport, capturing
  the outgoing request:
  - `list_issues()` requests path exactly `/api/issues/{project}/issues` with NO
    trailing slash.
  - `create_issue({...})` posts to `/api/issues/{project}/issues` with NO
    trailing slash.
  - A regression test: a MockTransport that returns 307 to the no-slash URL on a
    trailing-slash request results in a successful call (redirect followed), or
    simpler, assert the client is configured with follow_redirects=True.
  - Existing item/transition/import path tests still pass unchanged.

## Related Resources

- issuekit/client.py (`_issue_path`, `list_issues`, `create_issue`, httpx.Client).
- Found during the real migration (after #73). Server routes:
  mine-py/src/domains/issues/api/routes.py (`GET/POST /{project}/issues`).

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (340 passed, 25 skipped via uv run python -m pytest; +3 from prior, no test loss); issuekit check-encoding clean. Reviewed: _issue_path now maps an empty or "/" path to no suffix, so list_issues (GET) and create_issue (POST) hit /api/issues/{project}/issues with no trailing slash and no longer trigger the server's 307 redirect; item/sub-resource paths (/{number}, /claim, /claim-next, /import, /submit, etc.) are unchanged. The internal httpx.Client is now created with follow_redirects=True as defense-in-depth (preserves method+body and same-origin Authorization on 307/308), and the docstring tells injected clients to match. _url (auth login/logout) is unchanged. New tests assert the list and create request paths have no trailing slash. Scope limited to issuekit/client.py and tests/test_client.py. This unblocks migrate-to-api's verify step and all API-mode reads/creates against the real server. Verified live earlier: GET .../issues/ returns 307 to the no-slash route while .../issues and .../import behave correctly.`
