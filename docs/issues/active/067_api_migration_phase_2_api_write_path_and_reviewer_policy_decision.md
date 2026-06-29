---
id: 67
status: active
priority: medium
created: 2026-06-29
completed: 
stage: todo
author: claude
title: API migration phase 2: API write path and reviewer-policy decision
---

# Issue #67: API migration phase 2: API write path and reviewer-policy decision

Part of epic #64 (migrate issue storage to the mine-py API). This is phase 2
of 4 (write path), and it resolves the reviewer-policy decision.

## BLOCKED

Do not start until phase 1 (store seam + `ApiStore` reads) is merged.

## Goal

Route every WRITE / state transition through the API in API mode, and decide
how reviewer policy is owned now that the server enforces the state machine.

## Scope

1. Route transitions through the store/client (API mode):
   - `author` create: the SERVER allocates the id. Drop the local
     `get_next_issue_id` call and the filesystem `claim_lock` on the author
     path; `POST /api/issues/{project}/issues` returns the new issue with its id.
   - `claim` / `claim_next`, `submit_for_review`, `request_changes`,
     `approve` / `complete`: call the matching endpoints. `claim_next` maps a
     204 (empty) response to `None`.
   - Remove the filesystem `claim_lock` in API mode: the server serializes
     claims (`with_for_update` / `skip_locked`), so client-side locking is gone.
2. Behavior change - notes are rendered server-side:
   - The server appends `## Handoff`, `## Review Feedback`, and
     `## Completion Notes` from its event log. issuekit must STOP appending those
     sections to the body locally in API mode and instead pass the structured
     params: `summary` / `branch` / `commit` (submit), `notes` (request-changes),
     `summary` / `verification` (approve/complete). The local `_handoff_note` /
     `_review_feedback_note` helpers are filesystem-mode only.
3. Separation-of-duties is now enforced server-side (author != implementer,
   implementer != reviewer). The client surfaces the server's 409/422
   `{code,message}` as `WorkflowError`; do not duplicate the checks in API mode.

## Reviewer-policy decision (explicit deliverable)

The server hardcodes `IssueWorkflowConfig(default_reviewer="auto",
require_distinct_reviewer=True)` and exposes NO per-project policy. issuekit's
own config defaults differ (`default_reviewer="claude"`,
`require_distinct_reviewer=False`). These cannot both hold once the server owns
review transitions. Implement ONE option and document the choice in this issue:

- Option A (recommended, do this unless told otherwise): issuekit adopts the
  server policy in API mode. Stop resolving the reviewer client-side; let the
  server decide (auto + distinct). Update issuekit defaults/docs so API-backed
  projects expect "auto, distinct reviewer". Smallest change, no mine-py work.
- Option B: plumb per-project policy to the server. This needs a mine-py
  follow-up (a per-project policy field or a config endpoint). If chosen, file
  that proposal to mine-py and ship Option A as the interim behavior so this
  phase is not blocked.

Default to Option A. Record the decision and rationale in this issue before
submitting for review.

## Out of scope

- Removing filesystem code, indexes, docs, and the migration command (phase 3).
- Filesystem mode keeps its current behavior when `api_url` is unset.

## Test plan

- Each transition flow via `FakeIssuekitClient`: `author` returns the
  server-allocated id; `claim_next` returns `None` on 204; submit/request-changes
  /approve/complete advance state and pass structured params (no local note
  appending in API mode).
- Server-rejected transitions (self-implement, self-review, race lost) surface
  the server `message` as `WorkflowError`.
- Reviewer-policy behavior matches the chosen option.
- Full suite: `uv run python -m pytest`.

## Related

- Epic: #64. Depends on: phase 1.
- Server rules to match: `mine-py/src/domains/issues/services/
  issue_workflow_service.py` (transitions, `IssueWorkflowConfig`, self-review /
  self-implement guards).
- Next: phase 3 (cutover + migration + docs).
