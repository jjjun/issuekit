---
id: 67
status: completed
priority: medium
created: 2026-06-29
completed: 2026-06-29
stage: done
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

## Handoff

- Summary: Implemented by codex via issuekit implement.

## Review Feedback

- One contract regression to fix; the rest of the phase looks good (write paths gated on api_url, dual mode preserved, server-rendered notes, Option A reviewer policy in config + README). Issue: client.approve was changed to make reviewer optional and _drop_none it, but the server's IssueApproveRequest requires reviewer (str, min_length=1) in mine-py/src/domains/issues/schemas/issue.py. In API mode an approve without a concrete reviewer would return HTTP 422; tests only pass because FakeIssuekitClient.approve was made lenient. Fix: (1) Revert client.approve so reviewer is a required str again and is always sent (summary + verification + reviewer), restoring the phase-0 contract. (2) Ensure a concrete, non-'auto' reviewer always reaches the server in the API approve path. Pick one: either require an explicit reviewer in approve's API-mode branch and raise a clear WorkflowError when it is missing, OR resolve auto to a concrete distinct reviewer client-side (fetch the issue to get its implementer, then choose a configured assignee != implementer). Never send 'auto' or None as the reviewer. (3) Keep FakeIssuekitClient.approve's reviewer required so it mirrors the server, and add/adjust a test asserting the approve body always carries a concrete reviewer. Also please record the reviewer-policy decision (Option A) and its one-line rationale in this issue body, since the issue explicitly asked for that. Keep the full suite and issuekit check-encoding green.

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (344 passed, 22 skipped via uv run python -m pytest); issuekit check-encoding clean. Reviewed: all write transitions (author create, claim/claim_next, submit, request_changes, approve, complete) delegate to store/ApiStore->IssuekitClient when config.api_url is set, with the filesystem path (and claim_lock) unchanged in non-API mode (dual mode preserved). Notes are no longer appended locally in API mode; the server renders Handoff/Review Feedback/Completion from its event log. Reviewer-policy decision = Option A: in API mode config forces default_reviewer=auto and require_distinct_reviewer=true (load_config), documented in README; the server owns the policy. The phase-0 contract regression I flagged was fixed: client.approve requires reviewer again and always sends summary+verification+reviewer, FakeIssuekitClient.approve mirrors it (reviewer required), and approve's API branch resolves a concrete non-'auto' reviewer via _resolve_api_approval_reviewer (explicit --reviewer validated, else the issue assignee, else auto->distinct) and rejects 'auto'/None. index regeneration is skipped in API mode across author/approve/complete. New tests in test_workflow_cli.py cover the API write flows.`
