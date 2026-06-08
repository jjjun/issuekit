---
id: 44
status: completed
priority: medium
created: 2026-06-08
completed: 2026-06-08
stage: done
title: Document the end-to-end author-implement-review delegation cycle
---

# Issue #44: Document the end-to-end author-implement-review delegation cycle

## Problem

The individual handoffs exist as separate role protocols (author #34,
implementer, reviewer) and the `implement` command already chains
claim -> run -> submit_for_review (#39/#40). But there is no single description
of the full delegation cycle that ties them together, and no documentation of
the pull/pool model where any agent fills any role. An operator delegating work
to codex or kimi has to re-derive the sequence each time, which is the waste
this work set out to remove. With #42 and #43 making `author` a real,
agent-agnostic role, the cycle can finally be written down as one canonical
flow.

## Proposed Solution

Make the cycle a single documented, repeatable flow and update the prose that
still pins roles to specific agents.

1. Add an end-to-end description (in `protocol.py` and/or `docs/issues/README.md`)
   of: author -> open implement pool -> implement (claim/run/submit) -> review
   -> approve (complete) or request_changes (loop back to implement). State the
   separation-of-duties invariants (author != implementer; implementer !=
   reviewer; author == reviewer allowed) in one place.
2. Document the pull/pool model: authors leave issues unassigned so any idle
   agent claims them via `claim_next_task`, mirroring the open review pool, so no
   central orchestrator is required.
3. Update `CLAUDE.md` ("Claude writes proposals, codex-ready issues, and
   reviews") and `docs/issues/README.md` to describe roles, not fixed agent
   names, so codex/kimi authoring is first-class.
4. (Secondary, optional) Add a convenience `issuekit run-cycle <id> --agent X`
   that drives implement and, on `request_changes`, re-implements until approved
   -- only if it can reuse existing commands without new bypasses. Skip if it
   adds risk; the pull model already covers the multi-operator case.

## Impact

- `issuekit/protocol.py`: an end-to-end / cycle overview reachable via the
  protocol output.
- `docs/issues/README.md`: the delegation cycle and pool model, role-based
  wording.
- `CLAUDE.md`: replace agent-pinned wording with role-based wording.
- `issuekit/commands/run_cycle.py` (optional, new) and `issuekit/cli.py` if the
  convenience driver is included.
- `tests/`: protocol/doc rendering; run-cycle behavior if implemented.

## Implementation Plan

1. Write the end-to-end cycle text and the separation-of-duties invariants once.
2. Update `CLAUDE.md` and `docs/issues/README.md` to role-based wording.
3. (Optional) implement `run-cycle` only if it composes existing commands with
   no new self-review/self-implement bypass; otherwise document the pull model
   as the canonical multi-step path and drop the command.
4. Run `uv run pytest`, `uv run issuekit validate`, `uv run issuekit check-encoding`.

## Test Plan

- `uv run pytest tests/test_protocol.py`
- Manual: the protocol output (or README) describes the full cycle and the
  invariants; if `run-cycle` ships, it drives an issue from implement through
  approval without letting one session fill two conflicting roles.
- `uv run issuekit validate`

## Related Resources

- Issue #42 (author field + command) and Issue #43 (separation guard)
- Issue #34 (author protocol), #39/#40 (implement command + review gate)
- Issue #33 (open review pool; model for the open implement pool)
- `issuekit/protocol.py`, `docs/issues/README.md`, `CLAUDE.md`

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-08

## Completion Notes

- Approved by codex.
- Verification: `Reviewed by claude (distinct from implementer codex; open review pool). Adds a CYCLE_PROTOCOL overview prepended to every role render and an all-roles render; documents the author -> implementer -> reviewer pull model in issuekit/protocol.py, docs/issues/README.md, README.md, and the issues_README template. Separation-of-duties invariants are stated accurately, including the precise #43 behavior (explicit author self-assignment rejected while an open-pool same-name claim represents a distinct session) and author-may-review. De-pins agent names: CODEX_PROTOCOL/CLAUDE_PROTOCOL renamed to IMPLEMENTER_PROTOCOL/REVIEWER_PROTOCOL, claim_next_task(assignee=\"<agent>\") generalized, CLAUDE.md rewritten role-first. run-cycle command intentionally omitted per the issue's optional/secondary guidance (no new bypass; pull model covers the multi-operator case). No dangling references to the old constant names in code. Verified: uv run pytest (239 passed, 21 skipped), uv run issuekit validate (44 files, 0 warnings), uv run issuekit check-encoding clean.`
