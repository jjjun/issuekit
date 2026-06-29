---
id: 64
status: completed
priority: high
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: Migrate issue storage from local files to mine-py API backend
---

# Issue #64: Migrate issue storage from local files to mine-py API backend

## Problem

issuekit stores every issue as a Markdown file under
`docs/issues/{active,completed}/`, and allocates IDs locally as
`max(existing_id) + 1` (`issuekit/core.py:get_next_issue_id`). When the same
project is developed on two machines (today: a Windows host and an Ubuntu host),
both checkouts pick the same next number independently and the issue files
collide in git. The numbering is not a usable shared sequence and routine work
hits merge conflicts.

## Goal

Move the canonical issue store out of the repo and into the always-on mine-py
API. issuekit becomes an API client: mine-py owns issue IDs, content, metadata,
and state transitions. With one server allocating IDs and serializing claims,
cross-machine collisions and git conflicts disappear.

## Decisions (locked)

- Local Markdown files are ELIMINATED. The `docs/issues/{active,completed,indexes}`
  tree is removed from git. The API is the only store. No local mirror.
- The backend is PROJECT-SCOPED and MULTI-REPO. issuekit names its project in
  config; each consuming repo points at the same server with its own project key.
- Auth REUSES mine-py user auth (fast-domain FastAPI-Users JWT). The client logs
  in with service-account credentials and sends a Bearer token.
- mine-py REQUIRED: offline operation is not supported. Network/auth failures are
  hard errors with clear messages (acceptable: prod mine-py is always up).

## Dependency

This epic is BLOCKED on the mine-py server side. A proposal has been sent to
mine-py ("Provide an issue-tracking API (issuekit backend) in mine-py") defining
the data model, state machine, endpoints, auth, concurrency, and import. Do not
start client phases until that API contract is implemented and reachable. Once
the contract is final, split this epic into the per-phase issues below (each is
intended to be a single implementation-ready issue).

## Client architecture

- New module `issuekit/client.py`: `IssuekitClient`. Responsibilities:
  - resolve base URL + project from config/env;
  - log in via `POST /auth/login` with service-account credentials, cache the
    JWT, refresh on 401/expiry;
  - one method per endpoint (list / get / create / claim / claim-next / submit /
    request-changes / approve / complete);
  - map HTTP error codes (409/404/422) onto existing `WorkflowError` messages so
    CLI/MCP behavior and wording stay stable.
  - Pick a transport: prefer `httpx` (add dependency) or stdlib `urllib` to avoid
    a new dependency. Decide in phase 0.
- Storage seam: introduce a small store interface (Protocol) covering the reads
  and writes currently done against the filesystem (`read_active_issues`,
  `read_completed_issues`, `read_all_issues`, `find_for`, `get_next_issue_id`,
  and the workflow transitions). Implement it as `ApiStore` backed by
  `IssuekitClient`. Commands depend on the interface, not on files.
- `Issue` dataclass stays as the in-memory shape; `ApiStore` maps API JSON to
  `Issue`. The file-oriented fields (`file_path`, `relative_path`, `file_name`,
  `file_name_id`, `decode_error`) become optional / synthetic since there are no
  files. Audit every consumer of those fields (notably index links and
  `issue_dict`).

## Phased plan (each phase -> its own issue once unblocked)

1. Config + client foundation.
   - Add config: `api_url` (base URL), `project` (project key), auth via env
     (`ISSUEKIT_API_USER` / `ISSUEKIT_API_PASSWORD`, or a token var), request
     timeout. Wire into `IssuekitConfig` + `load_config` + `[tool.issuekit]`.
   - Implement `IssuekitClient` (login, token cache, error mapping) and a fake
     in-memory client for tests.

2. Read path over the API.
   - Add the store interface and `ApiStore`. Route read commands through it:
     `info`, `queue`, `validate` (becomes a server-consistency check, not a file
     check), the MCP `get_issue` / `list_queue` / `next_review` tools, and
     `find_for`. Virtualize the `Issue` file fields.

3. Write path over the API.
   - Route transitions through the client: `author` create (server allocates the
     ID; drop local `get_next_issue_id`), `claim` / `claim_next`,
     `submit_for_review`, `request_changes`, `approve` / `complete`. Remove the
     filesystem claim lock (`claim_lock`) since the server serializes claims.

4. Remove local store + migration + docs.
   - Provide `issuekit migrate-to-api`: export current
     `docs/issues/{active,completed}/*.md` to JSON, push to the mine-py import
     endpoint preserving IDs/dates, verify, then remove `docs/issues/` from the
     repo.
   - Retire what no longer applies: `generate-indexes` and the `indexes/` tree,
     file-based `validate` / `check-encoding` of issue files, and the
     implement-flow `docs/issues/` snapshot+restore guard in `agents/runner.py`
     (with no local tracker files, agents can no longer corrupt the tracker, so
     the guard from issue #52 becomes unnecessary).
   - Update `CLAUDE.md`, `docs/issues/README.md` (or replace it), the protocol
     text (`issuekit protocol`), and the MCP server instructions/resources to
     describe the API-backed model.

## Test plan

- Unit-test commands and workflow against the in-memory fake client (no network).
- Map-and-assert error translation (409 -> claim conflict, etc.).
- Optional integration test against a running mine-py, gated behind an env flag.
- Keep `core.py` frontmatter/parse tests only where still relevant (e.g. the
  migration exporter that still reads legacy `.md` files).
- Full suite green: `uv run python -m pytest`.

## Risks / notes

- This is a large, sequenced change; keep each phase shippable. Phases 1-3 can
  land while local files still exist (dual-read off, API on) to de-risk; phase 4
  is the irreversible cutover and should run the migration first.
- MCP server now requires network + valid credentials at startup/first call;
  surface auth/connection errors clearly rather than hanging.

## Related Resources

- mine-py proposal: "Provide an issue-tracking API (issuekit backend) in mine-py".
- `issuekit/core.py` (Issue fields, `get_next_issue_id`, `read_issues`).
- `issuekit/workflow.py` (transitions, guards, `claim_lock`, separation of duties).
- `issuekit/config.py` (`assignees`, `stages`, `default_reviewer`).
- `issuekit/agents/runner.py` (docs/issues snapshot+restore guard, issue #52).

**Completed**: 2026-06-29

## Completion Notes

- Decomposed into implementation-ready phase issues #65 (config + client), #66 (read path), #67 (write path + reviewer-policy decision), #68 (cutover + migration). This epic is a planning/tracking record, not directly implementable; work proceeds via #65-#68 in order.
- Verification: `Planning epic; no code. mine-py server side verified complete: issues domain shipped, 12 tests pass, router+migration wired. See #65-#68.`
