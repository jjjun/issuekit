---
id: 65
status: completed
priority: high
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: API migration phase 0: config and IssuekitClient HTTP client
---

# Issue #65: API migration phase 0: config and IssuekitClient HTTP client

Part of epic #64 (migrate issue storage to the mine-py API). This is phase 0
of 4 and has no predecessor; it is ready to implement now.

## Goal

Add the configuration and the HTTP client that every later phase depends on. No
command is rewired in this phase. Deliver `IssuekitClient` plus a reusable fake
for tests.

## Finalized server contract (mine-py, already shipped)

- Base path: `/api/issues/{project}/issues`.
- Endpoints: `POST /` (create), `GET /` (list, query: `status`, `stage`,
  `assignee`, `limit`, `offset`), `GET /{number}`, `POST /{number}/claim`,
  `POST /claim-next`, `POST /{number}/submit`, `POST /{number}/request-changes`,
  `POST /{number}/approve`, `POST /{number}/complete`, `POST /import`.
- Auth: fast-domain FastAPI-Users. All endpoints require a Bearer JWT
  (`require_user`); `import` requires an admin user (`require_admin`).
  Obtain the token via `POST /auth/login` with service-account credentials.
- Errors: JSON `{code, message}`. `code=not_found` -> 404; `invalid_project`,
  `invalid_agent`, `invalid_value` -> 422; everything else (invalid_transition,
  race_lost, forbidden_self_*) -> 409.
- `claim-next` returns 204 with an empty body when nothing is claimable.
- Response field `id` is the per-project issue number. Body is rendered
  server-side (Handoff / Review Feedback / Completion sections are appended by
  the server from an event log).

## Scope

1. Config (`issuekit/config.py`, `load_config`, `[tool.issuekit]`):
   - `api_url`: base URL of the mine-py server (e.g. `https://host`). Empty by
     default (filesystem mode stays the default until phase 3).
   - `project`: project key, validated as a workflow token
     (`^[a-z0-9][a-z0-9_-]{0,31}$`). Default `issuekit`.
   - `api_timeout`: request timeout seconds, default e.g. 30.
   - Credentials are read from the environment, NOT config files:
     `ISSUEKIT_API_USER` / `ISSUEKIT_API_PASSWORD` for the login flow, and an
     optional `ISSUEKIT_API_TOKEN` to inject a pre-obtained Bearer token (skips
     login). Never persist tokens to disk.
2. HTTP transport decision (make it here, document it): prefer `httpx` as a new
   dependency; if avoiding a new dependency is preferred, use stdlib
   `urllib.request`. Whatever is chosen must be mockable in tests.
3. `issuekit/client.py` -> `IssuekitClient`:
   - `login()` posts to `/auth/login`, caches the JWT in memory, reads expiry.
   - `_request(method, path, json=None)` attaches the Bearer header, and on a
     401 re-logs-in once and retries.
   - One method per endpoint: `list_issues`, `get_issue`, `create_issue`,
     `claim`, `claim_next`, `submit`, `request_changes`, `approve`, `complete`,
     `import_issues`. Methods return parsed JSON (dict / list[dict]); mapping to
     the `Issue` dataclass is phase 1.
   - Error mapping: non-2xx -> `WorkflowError` carrying the server `message`
     (and `code`). `claim_next` 204 -> return `None`.
   - All paths are built from `api_url` + `/api/issues/{project}/...`.
4. Test double: `FakeIssuekitClient` (e.g. `issuekit/testing.py`) implementing
   the same method surface against an in-memory dict, including atomic-ish id
   allocation and claim semantics, so phases 1-3 can unit-test without a server.

## Out of scope

- No command (`info`, `author`, `claim`, ...) is rewired yet.
- No removal of filesystem code. Filesystem mode remains the default.

## Test plan

- Config: new fields parse from `[tool.issuekit]`, defaults applied, `project`
  token validation rejects bad values.
- Client: request URL/headers/body shape; login + token caching; single 401 ->
  re-login -> retry; error mapping for 404/422/409 with `{code,message}`;
  `claim_next` 204 -> `None`. Use a mocked transport (e.g. respx for httpx, or a
  monkeypatched opener for urllib).
- `FakeIssuekitClient` round-trips create -> list -> get -> claim.
- Full suite: `uv run python -m pytest`.

## Related

- Epic: #64.
- Contract source on the server: `mine-py/src/domains/issues/`
  (`api/routes.py`, `schemas/issue.py`, `services/issue_workflow_service.py`).
- Next: phase 1 (read path) depends on this client.

## Handoff

- Summary: Implemented by codex via issuekit implement.

## Review Feedback

- client.py write-method request bodies do not match the shipped server schemas in mine-py/src/domains/issues/schemas/issue.py, which all set extra=forbid. As written, submit/approve/complete would return HTTP 422 in phase 2. Fix the three methods to match the contract exactly: (1) submit(): remove the 'assignee' field from both the body and the signature; IssueSubmitRequest accepts only summary, branch, commit, reviewer. (2) approve(): IssueApproveRequest REQUIRES summary, verification, and reviewer. Add required summary and verification params and send all three. (3) complete(): IssueCompleteRequest accepts only summary, verification, force; reviewer is forbidden. Replace the reviewer param with summary, verification, force and do not send reviewer. Add tests in tests/test_client.py that assert the exact JSON body sent for submit/approve/complete via the mocked transport. Everything else (config, WorkflowError code, login/401-retry, error mapping, FakeIssuekitClient) looks good; keep it. Full suite and issuekit check-encoding must stay green.

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (328 passed, 22 skipped via uv run python -m pytest); issuekit check-encoding clean (no BOM/CRLF/mojibake). Reviewed diff: config.py adds api_url/project/api_timeout with project-token validation; WorkflowError gains an optional code (backward compatible); client.py implements httpx-based IssuekitClient with /auth/login JWT caching, single 401 re-login+retry, and {code,message} (with FastAPI detail fallback) error mapping; paths build as /api/issues/{project}/issues. The three write methods now match the shipped server schemas exactly (submit drops assignee; approve sends required summary+verification+reviewer; complete sends summary+verification+force, no reviewer) with test_client.py asserting exact request bodies. FakeIssuekitClient provides an in-memory double for later phases. httpx pinned >=0.27,<1.`
