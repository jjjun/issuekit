# Commands

| Command | Purpose |
|---------|---------|
| `issuekit info [--json]` | Show API tracker status and effective agent configuration. |
| `issuekit show <id> [--json]` | Read one active or completed issue, including its body and handoff metadata, without changing it. |
| `issuekit next-review [--reviewer <name>] [--json]` | Read the next issue waiting for a reviewer without changing issue state. |
| `issuekit validate` | Check API connectivity and issue response shape. |
| `issuekit login [--user <username>]` | Authenticate to the API as the configured or specified user. |
| `issuekit logout` | Clear the saved API authentication session. |
| `issuekit profile [--project <name>] [--all] [--json]` | Show the stored profile for the local or specified project, or list all remote project profiles. |
| `issuekit author --title "..." (--body "..." \| --body-file <path>) --agent <agent> [--priority high\|medium\|low] [--assign <agent>] [--target-worker <worker.repo[@machine]>] [--allow-unregistered-worker] [--depends-on <ref>]... [--project <name>] [--direct-local-author] [--origin-project <name>] [--json]` | Create a new API-backed issue with an implementation-ready body. |
| `issuekit edit <id> [--title "..."] [--body "..." \| --body-file <path> \| --append "..." \| --append-file <path>] [--priority high\|medium\|low] [--depends-on <ref>]... [--force] [--json]` | Update an issue's title, body, priority, or dependency references. |
| `issuekit adopt <proposal-id> [--priority high\|medium\|low] [--append-file <path>] [--json]` | Adopt an incoming API proposal as an active issue, optionally appending scoped implementation text, and print the created API issue id. |
| `issuekit author-guard (show \| check \| clear) [--json]` | Diagnose or recover the local author-session separation-of-duties guard; with no action, `show` runs. See [Separation of duties](separation-of-duties.md). |
| `issuekit complete <id> [(--summary "..." \| --summary-file <path>)] [(--verification "..." \| --verification-file <path>)] [--force]` | Complete an issue through the API; use `--force` to close an active no-op, duplicate, obsolete, or anchor issue without claim and review ceremony. |
| `issuekit approve <id> (--verification "..." \| --verification-file <path>) [(--summary "..." \| --summary-file <path>)] [--reviewer <agent>]` | Approve a review-stage issue and move it to completed. |
| `issuekit claim [--assignee <agent>] [--priority high\|medium\|low] [--allow-author-session] [--allow-any-branch] [--no-sync]` | Claim the next active issue for an implementer. Without `--assignee`, the resolved default implementer is used. |
| `issuekit claim --id <id> [--assignee <agent>] [--allow-author-session] [--allow-any-branch] [--no-sync]` | Claim a specific active issue for an implementer. `--priority` cannot be combined with `--id`. |
| `issuekit claims [--worker <worker>] [--stage <stage>] [--json]` | List issue claims, optionally filtered by worker or workflow stage. |
| `issuekit implement <id> [--agent <agent>] [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--follow] [--allow-no-changes] [--allow-missing-report] [--allow-author-session] [--allow-any-branch] [--no-sync]` | Claim and run a configured implementer agent for an issue. |
| `issuekit submit-review <id> (--summary "..." \| --summary-file <path>) [--branch <branch>] [--commit <sha>] [--reviewer <agent>] [--allow-author-session] [--allow-any-branch]` | Submit implemented work to a reviewer. `--branch` (default: the current git branch) and `--commit` record where the implementation lives in the handoff metadata. |
| `issuekit review <id> --agent <agent> [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--follow]` | Run a configured reviewer agent for a review-stage issue. |
| `issuekit request-changes <id> (--notes "..." \| --notes-file <path>) [--assignee <agent>] [--reviewer <agent>]` | Return a reviewed issue to implementation. |
| `issuekit queue [--assignee <agent>] [--stage <stage>] [--json] [--with-body]` | List active issues, optionally filtered by assignee. `--with-body` requires `--json`. |
| `issuekit runs [<run-id>] [--active] [--json]` | Inspect an agent run or list runs, optionally limited to active ones. |
| `issuekit serve [--agent <agent>] [--model <model-id>] [--reasoning-effort <value>] [--interval <seconds>] [--heartbeat-interval <seconds>] [--max-heartbeat-failures <n>] [--priority high\|medium\|low] [--once] [--triage] [--review \| --proposal-checks] [--proposal-check-limit <n>] [--max-issues <n>] [--timeout-sec <seconds>] [--allow-any-branch] [--no-sync]` | Launch an agent loop that pulls from the implement pool by default, the review pool with `--review`, or proposal checks addressed to this worker with `--proposal-checks`; `--triage` and `--priority` apply only to the implement pool and are rejected with either mode flag. `--max-heartbeat-failures` stops serve after that many consecutive heartbeat failures (default 0, unlimited). |
| `issuekit orphans [--stale-after-sec <n>] [--json]` | List implementing issues whose claiming worker is gone or has stopped heartbeating. |
| `issuekit reclaim <id> [--force] [--stale-after-sec <n>] [--reason "..."] [--json]` | Return an orphaned or stale implementing claim to the implement pool. The worker heartbeat must be older than `--stale-after-sec` (default 300) unless `--force` skips that check. |
| `issuekit dispatch <id> --target-worker <worker.repo[@machine]> [--assignee <agent>] [--stage todo\|planned] [--allow-unregistered-worker] [--json]` | Direct a ready issue to a specific registered worker. |
| `issuekit readdress <id> [--reason "..."] [--json]` | Return a directed issue to the repo pool. |
| `issuekit check-encoding [--json] [--no-mojibake] [--no-halfwidth-kana] [--show-unconfirmed-mojibake] [--fail-on-unconfirmed] [--no-crlf] [--no-stray-cr] [--fix] [--changed [--base <ref>]] [--exclude <glob>]...` | Check tracked source files for BOM, CRLF, stray CR, and likely mojibake problems; the `--no-*` flags disable individual checks and `--fix` strips leading BOMs. `--changed` scans only changed files (or files differing from `--base`), and `--exclude` adds repo-relative POSIX glob exclusions. |
| `issuekit check-encoding --gate [--json]` | Reproduce the submit gate's mojibake verdict for the current worktree. `--gate` refuses every scan modifier listed in the row above. |
| `issuekit protocol [--agent <agent>] [--role author\|implementer\|pm\|reviewer\|triage]` | Print the canonical handoff protocol: every role with no flags, the agent's default role with `--agent`, or one role with `--role`. |
| `issuekit init [--force] [--with-mcp]` | Install tracker templates, encoding hooks, and optional MCP handoff scaffolding; `--force` overwrites existing templated files. |
| `issuekit setup [apply] [--force] [--json]` | Run per-repo MCP handoff scaffolding and setup diagnostics; `apply` is an explicit alias for the default action, and `--force` overwrites existing templated files. |
| `issuekit setup (check \| --check) [--json]` | Check setup state without writing files; the `--check` flag form is equivalent to the `check` action. |
| `issuekit dev-tool install-editable [--repo <path>] [--no-stop] [--json]` | Windows developer command to install this checkout as the global editable tool with the MCP extra. |
| `issuekit dev-tool reinstall [--repo <path>] [--no-stop] [--json]` | Windows developer recovery command to reinstall the global tool from an absolute checkout path. |
| `issuekit dev-tool reload-mcp [--json]` | Stop only running `issuekit-mcp.exe` processes; MCP clients own respawn and stdio reconnection. |
| `issuekit (add \| register) [--machine-id <id>] [--repo-id <id>] [--worker-id <id>] [--repo-description "..."] [--repo-metadata <key>=<value>]... [--worker-metadata <key>=<value>]... [--force]` | Register this git repo namespace and this checkout's worker (auto-derives repo and worker ids, with machine metadata, and publishes the configured API project). The id flags override the derived values, the metadata flags add or override published entries, and `--force` overwrites an existing pinned worker id or local collision. |
| `issuekit workers [--repo-id <id>] [--project <name>] [--json]` | List registered workers and their repo-level roles across projects. |
| `issuekit workers remove <worker.repo[@machine]> [--force] [--json]` | Remove a registered worker after checking for implementing issues. |
| `issuekit workers prune [--stale-after-sec <n>] [--dry-run] [--json]` | Remove stale workers that hold no implementing issue and are not targeted by directed work. |
| `issuekit repos remove <repo> [--json]` | Remove a repo catalog entry; the API refuses entries that still have references. |
| `issuekit add-ref <name> --path <repo> [--scope local\|workspace] [--path-to-workspace <file>]` | Register an optional local project alias. `--path-to-workspace` names an explicit workspace registry file for `--scope workspace`. |
| `issuekit list-refs` | List effective local project aliases and their source. |
| `issuekit negotiate --from-issue <id> --to <project> --initiator-side provider\|consumer --provider-agent <agent> --consumer-agent <agent> [--counterpart-ref <ref>] [--max-rounds <n>] [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--mock] [--json]` | Drive a bounded cross-project design negotiation (`--max-rounds` default 4 total turns, `--timeout-sec` default 120 per turn). Agents receive read-only instructions; issuekit rejects worktree, HEAD, and branch changes left by a turn. See [Cross-project negotiation](negotiation.md). |
| `issuekit negotiate --from-proposal <project>#proposal:<id> --initiator-side consumer --provider-agent <agent> --consumer-agent <agent> [--counterpart-ref <ref>] [--max-rounds <n>] [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--json]` | Lock a pending outbound proposal and use its title and body to seed a negotiation. `--mock` is rejected in this mode. |
| `issuekit negotiate --finalize <thread-id> (--to <project> \| --from-proposal <project>#proposal:<id>) [--author-agent <agent>] [--priority high\|medium\|low] [--mock] [--json]` | Create cross-linked implementation issues for an agreed thread. `--author-agent` defaults to the resolved default implementer and `--priority` to medium; `--from-issue` is rejected. |
| `issuekit negotiate --cancel <thread-id> (--from-proposal <project>#proposal:<id> \| --to <project>) [--json]` | Cancel a proposal-seeded negotiation and return its source proposal to recoverable pending triage. `--mock` is rejected. |
| `issuekit threads [<thread-id>] [--status negotiating\|agreed\|blocked\|cancelled] [--mock] [--json]` | Inspect or list cross-project negotiation threads; see [Cross-project negotiation](negotiation.md). |
| `issuekit propose [--to <project>\|<worker.repo[@machine]>] [--title "..."] [--body "..." \| --body-file <path>] [--from-issue <id>] [--reply <id>] [--blocking] [--depends-on <ref>]... [--agent <agent>] [--project <name>] [--json]` | Send a proposal to a project API inbox. `--to` is required unless `--reply` derives it from the adopted issue's origin; `--title` and the body default to the `--reply` or `--from-issue` issue, and `--reply` wins when both are given. |
| `issuekit incoming [--json]` | List inbound API proposals. |
| `issuekit outgoing --to <project> [--id <id>] [--status <status>] [--json]` | List proposals this project sent to a target project's inbox (read-only, self-scoped). |
| `issuekit discard <proposal-id> [--to <project>] [--json]` | Discard an incoming API proposal. With `--to`, discard a pending proposal this project sent to that target project's inbox instead. |
| `issuekit proposal-check-request --to <project> --proposal <id> [--worker <address>] [--json]` | Request evaluation of a pending proposal by a registered target worker. |
| `issuekit proposal-checks (--list \| --once) [--agent <agent>] [--model <model-id>] [--reasoning-effort <value>] [--status pending\|answered] [--timeout-sec <seconds>] [--limit <n>] [--offset <n>] [--check <id>] [--json]` | List or run proposal checks addressed to this worker. One of `--list` or `--once` is required; `--offset` works only with `--list`, and `--check` only with `--once`. |
| `issuekit triage --once [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--json]` | Launch a single agent triage loop that pulls pending inbound proposals. |
| `issuekit request [<text>] [--answer <request-id>] [--status [<request-id>]] [--inbox] [--target <project>] [--link <request-id>] [--json] [--dry-run] [--timeout-sec <seconds>] [--model <model-id>] [--reasoning-effort <value>]` | Route a PM request to project proposal inboxes; see [PM request router](pm-request.md). |

Text appended with `issuekit edit --append` or `--append-file` appears before
workflow-rendered Handoff, Review Feedback, and Completion Notes sections.

`issuekit info --json` includes `defaultReviewer`, the resolved
`defaultImplementer`, its raw `configuredDefaultImplementer` value, and
effective `agentRoles` including built-in role fallbacks. The text output shows
the same policy values and roles.
