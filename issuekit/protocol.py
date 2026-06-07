"""Canonical agent handoff protocol text."""

from __future__ import annotations


CODEX_PROTOCOL = """# Handoff protocol (codex)

Codex usually implements issuekit tasks from docs/issues/active/, but any
configured agent can be the implementer or the reviewer. The reviewer is the
agent assigned at stage=review and defaults to `default_reviewer`, which may be
`auto`. Same-name review is allowed unless `require_distinct_reviewer` is true.

Cross-project proposals are local suggestions under `docs/issues/incoming/`.
Before claiming normal work, inspect `issuekit incoming` when cross-repo
exchange is relevant. Adopt proposals only after local triage. When completing
an adopted issue with an `origin:` field, optionally send `issuekit propose
--reply <id>` so the origin repo receives a new inbound proposal; do not mutate
state in the origin repo.

When work reveals that a needed change belongs to another registered repo,
originate a proposal instead of only working around it locally or reporting it.
Use `issuekit list-refs` to find the target ref, then
`issuekit propose --to <ref> --title <t> --body <b>` (or the MCP `propose`
tool). Proposals are non-destructive suggestions in the target repo's
`incoming/`; the target repo owns triage, so do not mutate its state directly.

Proposal-system MCP and CLI share one implementation, so the CLI is a drop-in
fallback when the MCP tools hang or error. Equivalents (add `--json` for the
same structured output the MCP tools return):

- `propose(to, title, body)` -> `issuekit propose --to <ref> --title <t> --body <b> --json`
- `list_incoming()` -> `issuekit incoming --json`
- `adopt_proposal(file, priority)` -> `issuekit adopt <file> --priority <p> --json`

When the user asks codex to work on an issue in open-ended terms, such as
"handle the next issue" or "take the queue", do not wait for explicit
commands. Run this protocol end to end:

1. Call the issuekit MCP tool `claim_next_task(assignee="codex")`. The returned
   payload includes the issue body, which is the spec to implement. If it
   returns no issue, report that the queue is empty and stop.
2. Read the claimed issue, especially Problem, Implementation Plan, and Test
   Plan. Lay out a short plan with the files to change and the order of steps.
   Confirm the plan matches the issue scope before writing code; do not expand
   beyond it.
3. Implement the claimed issue on the current branch and make focused commits.
   Do not create or switch branches. The local workflow commits directly to
   main for speed; only create a branch when the user explicitly asks for one.
4. Run the relevant tests and `uv run issuekit check-encoding`.
5. Call `submit_for_review(id, summary, branch, commit, assignee="codex",
   reviewer=None)` with an ASCII summary, the current branch name, and the
   implementation commit. Set assignee to the implementer. Omit reviewer to use
   `default_reviewer`, or pass another configured assignee. If
   `default_reviewer` is `auto`, issuekit keeps the current review assignee when
   possible and chooses a distinct reviewer only when strict distinct review is
   required.
6. If a reviewer returns the issue with stage=changes_requested, call
   `claim_next_task(assignee="codex")` again, read the Review Feedback note,
   re-plan for just that feedback, address it, commit, and submit for review
   again.

Codex owns implementation unless assigned as reviewer. The reviewer owns the
review decision for issues assigned to them at stage=review.
"""


AUTHOR_PROTOCOL = """# Handoff protocol (author)

An author writes codex-ready issues and proposals. The author does not
implement issues.

When a needed change belongs to another registered repo, originate a proposal
instead of only reporting it. Use `issuekit list-refs` to find the target ref,
then `issuekit propose --to <ref> --title <t> --body <b>` (or the MCP
`propose` tool). Proposals are non-destructive suggestions in the target repo's
`incoming/`; the target repo owns triage, so do not mutate its state directly.

When the proposal-system MCP tools hang or error, fall back to the equivalent
CLI: `issuekit propose --to <ref> --title <t> --body <b> --json`,
`issuekit incoming --json`, and `issuekit adopt <file> --json`. They share the
same implementation and emit the same structured output.

When asked to write or plan an issue:

1. Run `issuekit info` to find the next issue id.
2. Create the issue under `docs/issues/active/` with `status: active`, an
   unstarted stage (empty or `todo`), and no assignee.
3. STOP. Do not call `claim_next_task` or implement the issue in the same
   session. An implementer claims it later via `claim_next_task`.
"""


CLAUDE_PROTOCOL = """# Handoff protocol (claude)

Claude usually reviews issuekit tasks after codex submits them, but any
configured reviewer can use this flow. The reviewer is the agent assigned at
stage=review and defaults to `default_reviewer`, which may be `auto`. Same-name
review is allowed unless `require_distinct_reviewer` is true.

When review reveals that a needed change belongs to another registered repo,
originate a proposal instead of only reporting it. Use
`issuekit list-refs` to find the target ref, then
`issuekit propose --to <ref> --title <t> --body <b>` (or the MCP `propose`
tool). Proposals are non-destructive suggestions in the target repo's
`incoming/`; the target repo owns triage, so do not mutate its state directly.

1. Call the issuekit MCP tool `next_review(reviewer=None)`. Omit reviewer to
   use `default_reviewer`, or pass the reviewer assignee to inspect. With
   `default_reviewer = "auto"`, omitted reviewer means the next issue already
   assigned at stage=review.
2. Review the referenced branch and commit diff against the issue body.
3. If the implementation is acceptable, call `approve(id, verification,
   reviewer=None)` with ASCII verification.
4. If changes are needed, call `request_changes(id, notes, reviewer=None,
   assignee=None)` with ASCII notes. Omit assignee to return the issue to its
   recorded implementer.

Claude owns proposals and codex-ready issues unless assigned as implementer.
The assigned reviewer owns the review decision.

When the proposal-system MCP tools hang or error, fall back to the equivalent
CLI: `issuekit propose --to <ref> --title <t> --body <b> --json`,
`issuekit incoming --json`, and `issuekit adopt <file> --json`. They share the
same implementation and emit the same structured output.
"""


_ROLE_PROTOCOLS = {
    "author": AUTHOR_PROTOCOL,
    "implementer": CODEX_PROTOCOL,
    "reviewer": CLAUDE_PROTOCOL,
}

_AGENT_ROLE = {
    "codex": "implementer",
    "claude": "reviewer",
}


def render_protocol(agent: str | None = None, role: str | None = None) -> str:
    """Render the handoff protocol for one agent/role, or both roles."""
    if agent is None and role is None:
        return f"{CODEX_PROTOCOL.rstrip()}\n\n{CLAUDE_PROTOCOL}"
    if role is not None:
        try:
            return _ROLE_PROTOCOLS[role]
        except KeyError as exc:
            raise ValueError(f"unknown role: {role}") from exc
    resolved_role = _AGENT_ROLE.get(agent, "implementer")
    return _ROLE_PROTOCOLS[resolved_role]
