---
id: 70
status: active
priority: medium
created: 2026-06-29
completed: 
stage: todo
author: claude
title: Persist API login: disk token cache with login/logout commands
---

# Issue #70: Persist API login: disk token cache with login/logout commands

## Problem

In API mode the IssuekitClient (issuekit/client.py) only caches the JWT in
process memory. Every `issuekit` CLI invocation is a fresh process, so it
re-logs-in each time and therefore needs ISSUEKIT_API_USER/ISSUEKIT_API_PASSWORD
present on every call. Operators doing routine issue work have to supply
credentials repeatedly, which is inconvenient.

The mine-py access token lifetime is 7 days
(`config.auth.access_token_expire_minutes = 7*24*60`, mine-py
src/mine_py/config_hooks/auth.py), so a persisted token can be reused for up to
a week without re-authenticating. fast-domain has no refresh-token flow; the
only credential is the access token from `POST /auth/login` (revocable via
`POST /auth/logout`).

## Goal

"Log in once, stay logged in." Persist the JWT to disk after login and reuse it
on later invocations until it expires, so credentials are not needed on every
call. Add explicit `issuekit login` / `issuekit logout` commands.

## Design

1. Token cache file:
   - Default path `~/.issuekit/token.json` (use `Path.home()`); allow override
     via env `ISSUEKIT_TOKEN_CACHE`.
   - Create the parent dir if missing; write the file with 0600 permissions
     (best-effort `os.chmod`; note Windows ACLs differ, so treat chmod failure
     as non-fatal but still avoid world-readable temp files - write atomically).
   - Contents are keyed by `api_url` so multiple servers do not clobber each
     other, e.g. `{ "<api_url>": {"token": "...", "expires_at": <epoch> } }`.
     Store `expires_at` from the login response (`expires_at`/`expires_in`) or
     the JWT `exp` (the client already parses these).

2. IssuekitClient changes:
   - On construction, attempt to load a cached token for this `api_url`; if it
     is present and not expired (reuse the existing skew in `_is_expired`), use
     it without calling `/auth/login`.
   - `login(force=False)`: if a valid token is available (memory or cache),
     return it. Otherwise, if credentials are available
     (username/password/env or ISSUEKIT_API_TOKEN), authenticate, then PERSIST
     the new token+expiry to the cache. If no credentials and no usable cache,
     raise a clear WorkflowError instructing the user to run `issuekit login`.
   - On a 401 retry, force re-login only when credentials are available; persist
     the refreshed token. If credentials are absent, surface the
     "run issuekit login" guidance rather than looping.
   - `ISSUEKIT_API_TOKEN` (injected token) keeps working and should NOT be
     written to the cache (it is externally managed).
   - Never log or print the full token.

3. New CLI commands (register in issuekit/cli.py COMMANDS and build_parser):
   - `issuekit login`: read username from `--user` or ISSUEKIT_API_USER; read
     password from ISSUEKIT_API_PASSWORD or, if absent and a TTY is available,
     prompt with `getpass` (never echo). Authenticate, write the cache, and
     print success with the human-readable expiry (not the token).
   - `issuekit logout`: POST `/auth/logout` with the cached token if present
     (ignore network errors), then delete the cache entry/file. Print confirmation.

4. Credentials-at-rest policy (default): persist ONLY the token, never the
   password. Fully unattended operation after expiry still works if
   ISSUEKIT_API_USER/PASSWORD are set in the environment (auto re-login). Do NOT
   add password-file storage in this issue.

## Out of scope

- Refresh tokens / PAT / API keys (would require a mine-py change).
- Changing the token lifetime.
- Encrypting the at-rest token (file perms only for now; http LAN is already
  plaintext).

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- New tests (mock the httpx transport for `/auth/login`, use a tmp cache path
  via ISSUEKIT_TOKEN_CACHE / monkeypatched home):
  - `login` writes a 0600 cache file with the token and expiry for the api_url.
  - A second client construction with the same api_url reuses the cached token
    and does NOT call `/auth/login`.
  - An expired cached token triggers re-login when credentials are present and
    refreshes the cache.
  - No cache and no credentials -> WorkflowError that names `issuekit login`.
  - `logout` removes the cache entry (and best-effort calls `/auth/logout`).
  - The cache is keyed by api_url (a different api_url does not reuse the token).
  - ISSUEKIT_API_TOKEN is used but not written to the cache.

## Related Resources

- issuekit/client.py (`IssuekitClient.login`, `_request` 401 retry,
  `_jwt_expiry`, `_response_expiry`, `_is_expired`).
- issuekit/cli.py (COMMANDS, build_parser).
- mine-py: `POST /auth/login`, `POST /auth/logout`; token lifetime 7 days
  (src/mine_py/config_hooks/auth.py).
- Epic context: #64 (mine-py API migration); follows #65 (client) and #67
  (write path).
