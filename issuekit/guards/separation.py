"""Shared separation-of-duties diagnostics and reference text."""

from __future__ import annotations

import re


SEPARATION_GUARD_REFERENCE = """Separation-of-duties guard reference:

| Guard | Separates | Enforced by | Error string | Recovery |
| --- | --- | --- | --- | --- |
| Author-session STOP guard | The checkout/session that ran `author` -> the same checkout/session directly claiming, implementing, or submitting that authored issue. Proposal guards record the handoff but do not block local issue lifecycle commands. | Client-side `issuekit.local.toml` `[author_guard]`, enforced by `enforce_no_author_guard`. Set `ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` to skip only this local enforcement while keeping the guard record visible. | `Author-session guard blocks <action>: STOP_NOW: this checkout authored issue <ref>...` | Stop and hand off the authored issue, or use `issuekit implement <id> --agent <agent>` to orchestrate a distinct implementer session. Direct lifecycle commands can pass `--allow-author-session` only for human emergency recovery. |
| Server author-implementer guard | Issue author identity/session -> issue implementer identity/session. | mine-py API server; issuekit sends optional `ISSUEKIT_SESSION` audit tokens but does not bypass the server decision. | `Issue #<id> was authored by <agent>; self-implementation is not allowed.` | Use a different implementer. Same-name delegation requires both author and implementer sessions to be recorded and distinct. `--allow-author-session` does not bypass this guard. See issuekit#162 and issuekit#163 for author-identity background. |
| Distinct-reviewer guard | Issue implementer -> auto-selected reviewer. Author == reviewer is allowed by design. | Client-side `require_distinct_reviewer` in `resolve_reviewer`; API-backed mode forces this local decision to true. | `Distinct-reviewer guard (require_distinct_reviewer) blocks auto reviewer resolution: no configured reviewer is distinct from the issue implementer.` | Configure an assignee distinct from `issue.implementer`. In non-API mode only, set `require_distinct_reviewer = false` if local policy permits. |
| Work-branch guard | Shared checkout handoff work -> the configured branch for that repo. | Client-side `[tool.issuekit] work_branch` or top-level `issuekit.toml` `work_branch`, enforced by `enforce_work_branch` before claim and submit lifecycle mutations. | `Work-branch guard blocks <action>: checkout is on branch '<cur>' but work_branch is '<want>'.` | Switch to the configured branch or update config. Lifecycle commands can pass `--allow-any-branch` only for human emergency recovery. |

Use this table to identify which guard fired before choosing a recovery path.
"""


AUTHOR_GUARD_HELP = f"""This command only manages the author-session STOP guard. It does not bypass the
mine-py server author-implementer guard, and it does not change
`require_distinct_reviewer` reviewer selection.

{SEPARATION_GUARD_REFERENCE}
"""


_SERVER_AUTHOR_IMPLEMENTER_RE = re.compile(
    r"^Issue #\d+ was authored by .+?(?:; self-implementation is not allowed\.)?$"
)


def separation_guard_note(message: str, *, code: str | None = None) -> str | None:
    """Return an additional diagnostic note for known guard errors."""

    if _SERVER_AUTHOR_IMPLEMENTER_RE.match(message.strip()):
        return (
            "Guard: server author-implementer guard (mine-py). This is not the "
            "local author-session STOP guard; `--allow-author-session` does not "
            "bypass it. Recovery: use a different implementer, or use "
            "`issuekit implement` with recorded distinct sessions for same-name "
            "delegation. See issuekit#162 and issuekit#163 for author-identity "
            "background."
        )
    if code == "distinct_reviewer_guard":
        return (
            "Guard: distinct-reviewer guard (`require_distinct_reviewer`). "
            "This compares against `issue.implementer`, not the author."
        )
    return None
