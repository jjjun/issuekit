---
id: 66
status: completed
priority: medium
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: API migration phase 1: store seam and ApiStore read path
---

# Issue #66: API migration phase 1: store seam and ApiStore read path

Part of epic #64 (migrate issue storage to the mine-py API). This is phase 1
of 4 (read path).

## BLOCKED

Do not start until phase 0 (config + `IssuekitClient` + `FakeIssuekitClient`)
is merged. This phase consumes that client.

## Goal

Introduce a storage seam and an API-backed implementation, then route every
READ through it. Writes stay on the filesystem until phase 2. The seam lets
phases 1-3 land while local files still exist (dual mode), de-risking the
cutover.

## Scope

1. Store seam (`issuekit/store.py`): a `Protocol` (or ABC) covering the current
   read surface:
   - `read_active_issues()`, `read_completed_issues()`, `read_all_issues()`,
   - `get_issue(issue_id)`,
   - `find_for(assignee=None, stage=None)`.
   Provide `get_store(config)` factory: returns `ApiStore` when `config.api_url`
   is set, else the existing filesystem behavior wrapped as `FilesystemStore`.
2. `ApiStore` (backed by `IssuekitClient`): map the server `IssueResponse` JSON
   onto the existing `Issue` dataclass.
   - `id` (server number) -> `Issue.id`; `body` -> `frontmatter.body` /
     `content`; `status` -> `issue_status`; `priority`, `stage`, `assignee`,
     `implementer`, `author`, `created`, `completed` map directly.
   - Virtualize the file-oriented fields: `file_name`, `file_path`,
     `relative_path`, `file_name_id` become synthetic (e.g.
     `relative_path = f"{project}#{id}"`), and `decode_error` is always `False`.
     Audit every consumer of those fields (notably `issue_dict`'s `file` key and
     any index link rendering) and keep their output sane with synthetic refs.
3. Route reads through the store:
   - CLI: `info`, `queue`.
   - `validate`: in API mode it is no longer a file check; convert it to a
     connectivity + well-formedness check (fetch the list, assert reachable and
     that required fields are present). Filesystem mode keeps today's checks.
   - MCP tools: `get_issue`, `list_queue`, `next_review`.
   - `workflow.find_for` reads via the store.

## Out of scope

- Writes/transitions still go through the filesystem path (phase 2).
- No removal of filesystem code, indexes, or docs (phase 3).

## Test plan

- `ApiStore` JSON -> `Issue` mapping, including synthetic file fields and
  `issue_dict` output, driven by `FakeIssuekitClient`.
- `info` / `queue` / MCP read tools produce the same payload shape in API mode
  as in filesystem mode (compare against fixtures).
- `get_store` returns `ApiStore` only when `api_url` is set.
- Full suite: `uv run python -m pytest`.

## Related

- Epic: #64. Depends on: phase 0.
- Server contract: `GET /api/issues/{project}/issues` and
  `GET /api/issues/{project}/issues/{number}` (see phase 0 for the full list).
- Next: phase 2 (write path) builds on this seam.

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (338 passed, 22 skipped via uv run python -m pytest); issuekit check-encoding clean. Reviewed: store.py defines IssueStore Protocol with FilesystemStore (wraps existing read_* helpers) and ApiStore (maps server IssueResponse JSON to Issue, validates required fields, synthesizes file_name/file_path/relative_path as '{project}#{id}', decode_error always False, title falls back to heading then 'Issue #id'); get_store(config) returns ApiStore when api_url is set else FilesystemStore. Reads routed through the store: info/queue CLI, validate (api_url branch does a connectivity+well-formedness check), and MCP get_issue/list_queue/next_review (the latter two via the now store-backed workflow.find_for, which uses a local import to avoid a circular dependency). queue/validate widened to catch ValueError from API mapping. Write paths and the cross-project proposal adopt flow correctly remain filesystem-backed (phase 2 / deferred). Dual mode preserved: filesystem behavior unchanged when api_url is empty.`
