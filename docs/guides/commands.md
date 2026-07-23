# Commands

| Command | Purpose |
|---------|---------|
| `issuekit info [--json]` | Show API tracker status and effective agent configuration. |
| `issuekit validate` | Check API connectivity and issue response shape. |
| `issuekit complete <id> --summary "..." --verification "..." [--force]` | Complete an issue through the API; use `--force` to close an active no-op, duplicate, obsolete, or anchor issue without claim and review ceremony. |
| `issuekit approve <id> --verification "..." [--reviewer claude]` | Approve a review-stage issue and move it to completed. |
| `issuekit claim --assignee codex` | Claim the next active issue for an implementer. |
| `issuekit claim --id <id> --assignee codex` | Claim a specific active issue for an implementer. |
| `issuekit submit-review <id> --summary "..." [--assignee codex] [--reviewer claude]` | Submit implemented work to a reviewer. |
| `issuekit request-changes <id> --notes "..." [--assignee codex] [--reviewer claude]` | Return a reviewed issue to implementation. |
| `issuekit queue --assignee claude [--stage review]` | List active issues for an assignee. |
| `issuekit orphans [--stale-after-sec <n>] [--json]` | List implementing issues whose claiming worker is gone or has stopped heartbeating. |
| `issuekit reclaim <id> [--force] [--reason "..."] [--json]` | Return an orphaned or stale implementing claim to the implement pool. |
| `issuekit readdress <id> [--reason "..."] [--json]` | Return a directed issue to the repo pool. |
| `issuekit check-encoding [--json] [--fail-on-unconfirmed]` | Check tracked source files for leading BOM bytes and likely mojibake. |
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
| `issuekit propose --to <project> --title "..."` | Send a proposal to a project API inbox. |
| `issuekit incoming [--json]` | List inbound API proposals. |
| `issuekit outgoing --to <project> [--id <id>] [--status <status>]` | List proposals this project sent to a target project's inbox (read-only, self-scoped). |
| `issuekit adopt <proposal-id> [--json]` | Adopt an incoming API proposal as a local issue and print the created API issue id. |
| `issuekit discard <proposal-id>` | Discard an incoming API proposal. |
| `issuekit request <text> [--model <model-id>] [--reasoning-effort <value>]` | Route a PM request to project proposal inboxes. |

`issuekit info --json` includes `defaultReviewer`, the resolved
`defaultImplementer`, its raw `configuredDefaultImplementer` value, and
effective `agentRoles` including built-in role fallbacks. The text output shows
the same policy values and roles.
