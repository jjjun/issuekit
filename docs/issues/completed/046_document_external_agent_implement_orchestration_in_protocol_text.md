---
id: 46
status: completed
priority: medium
created: 2026-06-08
completed: 2026-06-08
stage: done
author: claude
title: Document external-agent implement orchestration in protocol text
---

# Issue #46: Document external-agent implement orchestration in protocol text

## Problem

infra-toolkit used `issuekit implement <id> --agent <agent>` end to end on their
issue #120 and reported that the orchestration entry point is undiscoverable
from protocol text. The implementer protocol emphasizes `claim_next_task` and
`submit_for_review`; the CLI command that launches a configured external agent,
`issuekit implement <id> --agent <agent>`, was found only via
`issuekit implement --help`. Two related documentation gaps: approving a
review-stage issue currently maps to `issuekit complete` (non-obvious), and the
`.agent-runs/` log directory handling is undocumented.

Origin: infra-toolkit#0@10762a8.

## Proposed Solution

Document the external-agent orchestration path in protocol text and README
without changing behavior.

1. In the cycle/orchestrator-facing protocol, state that an orchestrator (or
   author) may drive an external implementer with
   `issuekit implement <id> --agent <agent> --timeout-sec <n>`, which claims or
   operates on the assigned issue, runs the configured agent, and submits for
   review. Position it alongside the pull-model `claim_next_task` path so both
   the push (drive an external agent) and pull (idle agent claims) entries are
   visible.
2. In the reviewer protocol, state that approving a review-stage issue is done
   through the reviewer flow (MCP `approve`, or CLI `complete` / the `approve`
   alias once it lands), and `request_changes` for incomplete work.
3. Document `.agent-runs/` as ignored agent run logs kept out of commits and
   useful for review (the init scaffold is handled separately).

## Impact

- `issuekit/protocol.py`: orchestrator and reviewer documentation additions.
- `docs/issues/README.md`: external-agent orchestration plus a `.agent-runs/`
  note.
- `tests/test_protocol.py`: assert the new guidance strings render.

## Implementation Plan

1. Add orchestrator guidance mentioning `issuekit implement <id> --agent <agent>`
   to the protocol output (cycle or implementer/reviewer text).
2. Add reviewer approve / request-changes mapping wording.
3. Add a `.agent-runs/` handling note to `docs/issues/README.md`.
4. Update or extend tests for the new strings.
5. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_protocol.py`
- Manual: protocol output mentions `issuekit implement` and the reviewer approve
  mapping.
- `uv run issuekit validate`

## Related Resources

- Origin proposal: infra-toolkit#0@10762a8
  (`docs/issues/incoming/infra_toolkit__0__improve_issuekit_external_agent_implement_review_orchestration.md`)
- `issuekit/protocol.py`, `docs/issues/README.md`
- Sibling adoptions: the CLI `approve` alias issue and the implement-robustness
  issue from the same proposal

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-08

## Completion Notes

- Approved by codex.
- Verification: `Reviewed by claude (distinct from implementer codex; open review pool). CYCLE_PROTOCOL now documents the orchestrator push entry issuekit implement <id> --agent <agent> --timeout-sec <n> (claims/operates on the assigned issue, launches the configured agent, submits for review), visible across all role renders and the lean server instructions; README mirrors it alongside the pull model. REVIEWER_PROTOCOL maps approval to the MCP approve tool or CLI issuekit complete, forward-referencing the issuekit approve alias as conditional (once available) so it does not claim an unshipped command exists; request_changes covers incomplete work. README documents .agent-runs/ as gitignored run-log storage kept out of commits but useful for review, and clarifies the MCP-approve vs CLI-complete spelling. Tests assert the new orchestrator and reviewer strings render. Verified: uv run pytest (242 passed, 21 skipped), uv run issuekit validate (48 files, 0 warnings), uv run issuekit check-encoding clean. Docs-only change; no behavior modified.`
