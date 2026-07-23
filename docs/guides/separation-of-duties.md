# Separation-of-duties guards

issuekit has four separation-of-duties guards. Use this table to identify
which guard blocked a command before choosing a recovery path. The same
canonical reference appears in `issuekit protocol` output and
`issuekit author-guard --help`.

| Guard | Separates | Enforced by | Error string | Recovery |
| --- | --- | --- | --- | --- |
| Author-session STOP guard | The checkout/session that ran `author` -> the same checkout/session claiming, implementing, or submitting that authored issue. Proposal guards record the handoff but do not block local issue lifecycle commands. | Client-side `issuekit.local.toml` `[author_guard]`, enforced by `enforce_no_author_guard`. Set `ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` to skip only this local enforcement while keeping the guard record visible. | `Author-session guard blocks <action>: STOP_NOW: this checkout authored issue <ref>...` | Stop and hand off the authored issue. After handoff, run `issuekit author-guard clear`; lifecycle commands can pass `--allow-author-session` only for human emergency recovery. |
| Server author-implementer guard | Issue author identity -> issue implementer identity. | mine-py API server; issuekit does not configure or bypass it. | `Issue #<id> was authored by <agent>; self-implementation is not allowed.` | Use a different implementer. `--allow-author-session` does not bypass this guard. See issuekit#162 and issuekit#163 for the in-flight author-identity work. |
| Distinct-reviewer guard | Issue implementer -> auto-selected reviewer. Author == reviewer is allowed by design. | Client-side `require_distinct_reviewer` in `resolve_reviewer`; API-backed mode forces this local decision to true. | `Distinct-reviewer guard (require_distinct_reviewer) blocks auto reviewer resolution: no configured reviewer is distinct from the issue implementer.` | Configure an assignee distinct from `issue.implementer`. In non-API mode only, set `require_distinct_reviewer = false` if local policy permits. |
| Work-branch guard | Shared checkout handoff work -> the configured branch for that repo. | Client-side `[tool.issuekit] work_branch` or top-level `issuekit.toml` `work_branch`, enforced by `enforce_work_branch` before claim and submit lifecycle mutations. | `Work-branch guard blocks <action>: checkout is on branch '<cur>' but work_branch is '<want>'.` | Switch to the configured branch or update config. Lifecycle commands can pass `--allow-any-branch` only for human emergency recovery. |
