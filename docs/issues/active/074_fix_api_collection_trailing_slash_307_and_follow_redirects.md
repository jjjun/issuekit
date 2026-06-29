---
id: 74
status: active
priority: high
created: 2026-06-29
completed: 
stage: todo
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
