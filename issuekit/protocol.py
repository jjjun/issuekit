"""Canonical agent handoff protocol text."""

from __future__ import annotations


CODEX_PROTOCOL = """# Handoff protocol (codex)

Codex usually implements issuekit tasks from docs/issues/active/, but any
configured agent can be the implementer or the reviewer. The reviewer is the
agent assigned at stage=review, defaults to `default_reviewer`, and must not
match the issue's `implementer`.

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
   `default_reviewer`, or pass another configured assignee.
6. If a reviewer returns the issue with stage=changes_requested, call
   `claim_next_task(assignee="codex")` again, read the Review Feedback note,
   re-plan for just that feedback, address it, commit, and submit for review
   again.

Codex owns implementation unless assigned as reviewer. The reviewer owns the
review decision for issues assigned to them at stage=review.
"""


CLAUDE_PROTOCOL = """# Handoff protocol (claude)

Claude usually reviews issuekit tasks after codex submits them, but any
configured reviewer can use this flow. The reviewer is the agent assigned at
stage=review, defaults to `default_reviewer`, and must not match the issue's
`implementer`.

1. Call the issuekit MCP tool `next_review(reviewer=None)`. Omit reviewer to
   use `default_reviewer`, or pass the reviewer assignee to inspect.
2. Review the referenced branch and commit diff against the issue body.
3. If the implementation is acceptable, call `approve(id, verification,
   reviewer=None)`.
4. If changes are needed, call `request_changes(id, notes, reviewer=None,
   assignee=None)` with ASCII notes. Omit assignee to return the issue to its
   recorded implementer.

Claude owns proposals and codex-ready issues unless assigned as implementer.
The assigned reviewer owns the review decision.
"""


PROTOCOLS = {
    "codex": CODEX_PROTOCOL,
    "claude": CLAUDE_PROTOCOL,
}


def render_protocol(agent: str | None = None) -> str:
    """Render the handoff protocol for one agent, or both agents."""
    if agent is None:
        return f"{CODEX_PROTOCOL.rstrip()}\n\n{CLAUDE_PROTOCOL}"
    try:
        return PROTOCOLS[agent]
    except KeyError as exc:
        raise ValueError(f"unknown agent: {agent}") from exc
