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
| `issuekit author --title "..." (--body "..." \| --body-file <path>) --agent <agent> [--priority high\|medium\|low] [--assign <agent>] [--target-worker <worker.repo[@machine]>] [--allow-unregistered-worker] [--depends-on <ref>] [--project <name>] [--direct-local-author] [--origin-project <name>] [--json]` | Create a new API-backed issue with an implementation-ready body. |
| `issuekit edit <id> [--title "..."] [--body "..." \| --body-file <path> \| --append "..." \| --append-file <path>] [--priority high\|medium\|low] [--depends-on <ref>] [--force] [--json]` | Update an issue's title, body, priority, or dependency references. |
| `issuekit author-guard show\|check\|clear` | Diagnose or recover the local author-session separation-of-duties guard; see [Separation of duties](separation-of-duties.md). |
| `issuekit complete <id> --summary "..." --verification "..." [--force]` | Complete an issue through the API; use `--force` to close an active no-op, duplicate, obsolete, or anchor issue without claim and review ceremony. |
| `issuekit approve <id> --verification "..." [--reviewer claude]` | Approve a review-stage issue and move it to completed. |
| `issuekit claim --assignee codex` | Claim the next active issue for an implementer. |
| `issuekit claim --id <id> --assignee codex` | Claim a specific active issue for an implementer. |
| `issuekit claims [--worker <worker>] [--stage <stage>] [--json]` | List issue claims, optionally filtered by worker or workflow stage. |
| `issuekit implement <id> [--agent <agent>] [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--follow] [--allow-no-changes] [--allow-author-session] [--allow-any-branch] [--no-sync]` | Claim and run a configured implementer agent for an issue. |
| `issuekit submit-review <id> --summary "..." [--reviewer claude]` | Submit implemented work to a reviewer. |
| `issuekit review <id> --agent <agent> [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--follow]` | Run a configured reviewer agent for a review-stage issue. |
| `issuekit request-changes <id> --notes "..." [--assignee codex] [--reviewer claude]` | Return a reviewed issue to implementation. |
| `issuekit queue --assignee claude [--stage review]` | List active issues for an assignee. |
| `issuekit runs [<run-id>] [--active] [--json]` | Inspect an agent run or list runs, optionally limited to active ones. |
| `issuekit serve [--agent <agent>] [--model <model-id>] [--reasoning-effort <value>] [--interval <seconds>] [--heartbeat-interval <seconds>] [--priority high\|medium\|low] [--once] [--triage] [--review] [--proposal-checks] [--proposal-check-limit <n>] [--max-issues <n>] [--timeout-sec <seconds>] [--allow-any-branch] [--no-sync]` | Launch an agent loop that pulls from the implement pool by default, or the review pool with `--review`. |
| `issuekit orphans [--stale-after-sec <n>] [--json]` | List implementing issues whose claiming worker is gone or has stopped heartbeating. |
| `issuekit reclaim <id> [--force] [--reason "..."] [--json]` | Return an orphaned or stale implementing claim to the implement pool. |
| `issuekit dispatch <id> --target-worker <worker.repo[@machine]> [--assignee <agent>] [--stage todo\|planned] [--allow-unregistered-worker] [--json]` | Direct a ready issue to a specific registered worker. |
| `issuekit readdress <id> [--reason "..."] [--json]` | Return a directed issue to the repo pool. |
| `issuekit check-encoding [--json] [--fail-on-unconfirmed] [--gate]` | Check tracked source files for encoding problems, or reproduce the submit-gate mojibake verdict with `--gate`. |
| `issuekit protocol [--agent codex\|claude]` | Print the canonical handoff protocol. |
| `issuekit init [--with-mcp]` | Install tracker templates, encoding hooks, and optional MCP handoff scaffolding. |
| `issuekit setup [--force] [--json]` | Run per-repo MCP handoff scaffolding and setup diagnostics. |
| `issuekit setup check --json` | Check setup state without writing files. |
| `issuekit dev-tool install-editable [--repo <path>] [--no-stop] [--json]` | Windows developer command to install this checkout as the global editable tool with the MCP extra. |
| `issuekit dev-tool reinstall [--repo <path>] [--no-stop] [--json]` | Windows developer recovery command to reinstall the global tool from an absolute checkout path. |
| `issuekit dev-tool reload-mcp [--json]` | Stop only running `issuekit-mcp.exe` processes; MCP clients own respawn and stdio reconnection. |
| `issuekit add` / `issuekit register` | Register this git repo namespace and this checkout's worker (auto-derives repo and worker ids, with machine metadata, and publishes the configured API project). |
| `issuekit workers [--repo-id <id>] [--project <name>] [--json]` | List registered workers and their repo-level roles across projects. |
| `issuekit workers remove <worker.repo[@machine]> [--force] [--json]` | Remove a registered worker after checking for implementing issues. |
| `issuekit workers prune [--stale-after-sec <n>] [--dry-run] [--json]` | Remove stale workers that hold no implementing issue and are not targeted by directed work. |
| `issuekit repos remove <repo> [--json]` | Remove a repo catalog entry; the API refuses entries that still have references. |
| `issuekit add-ref <name> --path <repo> [--scope local\|workspace]` | Register an optional local project alias. |
| `issuekit list-refs` | List effective local project aliases and their source. |
| `issuekit negotiate --from-issue <id> --to <project> --initiator-side <provider\|consumer> --provider-agent <agent> --consumer-agent <agent> [--counterpart-ref <ref>]` | Drive a bounded cross-project design negotiation. Agents receive read-only instructions; issuekit rejects worktree, HEAD, and branch changes left by a turn. See [Cross-project negotiation](negotiation.md). |
| `issuekit negotiate --from-proposal <project>#proposal:<id> --initiator-side consumer --provider-agent <agent> --consumer-agent <agent>` | Lock a pending outbound proposal and use its title and body to seed a negotiation. |
| `issuekit negotiate --cancel <thread-id> --from-proposal <project>#proposal:<id>` | Cancel a proposal-seeded negotiation and return its source proposal to recoverable pending triage. |
| `issuekit threads [<thread-id>] [--status negotiating\|agreed\|blocked\|cancelled] [--mock] [--json]` | Inspect or list cross-project negotiation threads; see [Cross-project negotiation](negotiation.md). |
| `issuekit propose --to <project> --title "..."` | Send a proposal to a project API inbox. |
| `issuekit incoming [--json]` | List inbound API proposals. |
| `issuekit outgoing --to <project> [--id <id>] [--status <status>]` | List proposals this project sent to a target project's inbox (read-only, self-scoped). |
| `issuekit adopt <proposal-id> [--json]` | Adopt an incoming API proposal as a local issue and print the created API issue id. |
| `issuekit discard <proposal-id>` | Discard an incoming API proposal. |
| `issuekit proposal-check-request --to <project> --proposal <id> [--worker <address>] [--json]` | Request evaluation of a pending proposal by a registered target worker. |
| `issuekit proposal-checks [--agent <agent>] [--model <model-id>] [--reasoning-effort <value>] [--list \| --once] [--status pending\|answered] [--timeout-sec <seconds>] [--limit <n>] [--offset <n>] [--json]` | List or run proposal checks addressed to this worker. |
| `issuekit triage --once [--model <model-id>] [--reasoning-effort <value>] [--timeout-sec <seconds>] [--json]` | Launch a single agent triage loop that pulls pending inbound proposals. |
| `issuekit request [<text>] [--answer <request-id>] [--status [<request-id>]] [--inbox] [--target <project>] [--link <request-id>] [--json] [--dry-run] [--timeout-sec <seconds>] [--model <model-id>] [--reasoning-effort <value>]` | Route a PM request to project proposal inboxes; see [PM request router](pm-request.md). |

`issuekit info --json` includes `defaultReviewer`, the resolved
`defaultImplementer`, its raw `configuredDefaultImplementer` value, and
effective `agentRoles` including built-in role fallbacks. The text output shows
the same policy values and roles.
