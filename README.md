# issuekit

`issuekit` is a language-neutral CLI for sharing an API-backed issue handoff
workflow across repositories. Issue lifecycle state and cross-project proposals
live in a mine-py API project.

## Install

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

Install with the `mcp` extra when codex or Claude Code will use the handoff MCP
server. Without the extra, `issuekit-mcp` cannot start.

For local development:

```powershell
uv sync
uv run issuekit --help
uv run issuekit dev-tool install-editable
```

## Tests

Run the normal project gates by hand before publishing changes:

```powershell
uv run pytest
uv run issuekit check-encoding
```

The default pytest suite is intended for offline development and CI. Tests that
would call a live issuekit API backend are marked `live_contract` and skip
unless explicitly enabled.

Run only the live contract checks with a reachable delete-safe test backend:

```powershell
$env:ISSUEKIT_RUN_LIVE_CONTRACTS = "1"
$env:ISSUEKIT_API_URL = "https://mine.example"
uv run pytest -m live_contract -q
```

`ISSUEKIT_RUN_LIVE_CONTRACTS=1` is the opt-in switch. `ISSUEKIT_API_URL` must
point at the backend under test; individual future live contract tests may also
use the normal API credential variables, such as `ISSUEKIT_API_TOKEN` or
`ISSUEKIT_API_USER` and `ISSUEKIT_API_PASSWORD`, when they hit authenticated
endpoints.

Maintainers can also run the same pytest and encoding checks from GitHub
Actions with the manual `Tests` workflow and the `Run workflow` button. These
checks are intentionally not an automatic CI gate on push or pull request.

On Windows, `dev-tool install-editable` installs the global `issuekit` and
`issuekit-mcp` tool shims from this checkout in editable mode. It stops stale
`issuekit-mcp.exe` processes first, uninstalls any existing global `issuekit`
tool if present, installs with the `mcp` extra, and verifies the resulting tool
environment.

## MCP server

Install or upgrade the MCP server once as a global tool:

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

When upgrading a global tool, stop running issuekit MCP servers first. MCP
clients hold `issuekit-mcp.exe` while the session is running, so replacing the
tool underneath them can leave the install half-removed. Normal users should
prefer:

```powershell
uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"
```

Use an absolute path or URL, not a bare `.`, to avoid cwd-dependent installs.
After any reinstall or upgrade, restart running codex and Claude Code sessions;
they keep a stdio connection to the old server process.

Issuekit developers working from a Windows checkout should use the repeatable
developer commands instead:

```powershell
uv run issuekit dev-tool install-editable
uv run issuekit dev-tool reload-mcp
uv run issuekit dev-tool reinstall
```

`install-editable` reflects source edits the next time a global `issuekit` or
`issuekit-mcp` process starts. `reload-mcp` stops only matching
`issuekit-mcp.exe` processes and reports their PIDs and executable paths when
available. It cannot respawn or reconnect an already-open codex or Claude Code
stdio MCP transport; the MCP client owns that connection. If MCP tools still
return `Transport closed` after `reload-mcp`, reload or restart the MCP client
session, reload the thread/window if supported, or start a fresh session so the
client can spawn a new `issuekit-mcp` transport. `reinstall` is the recovery
path when editable metadata gets stale or a global tool environment is
partially broken. All generated uv install commands use an absolute checkout
path, never a bare `.`.

Then scaffold each repository that uses issuekit:

```powershell
issuekit setup
```

This runs `init --with-mcp`, prints setup diagnostics, and shows the optional
global codex MCP-store command:

```powershell
codex mcp add issuekit -- issuekit-mcp
```

That global command is unnecessary when codex reads the repo's
`.codex/config.toml`, but it is useful for users who manage MCP servers through
the global codex store. `issuekit setup` only edits files inside the current
repo. It never kills processes and never edits global codex config.

Automation should use the stable JSON contract:

```powershell
issuekit setup --json
```

This still scaffolds the repo, but prints one JSON object with `ok`, `scaffold`,
and `diagnostics` fields instead of the human checklist. `ok: false` means at
least one diagnostic still needs action, often an optional global install or
configuration step; the command still exits 0 when repo scaffolding succeeds.

Orchestrators that only need a preflight should use the read-only check:

```powershell
issuekit setup check --json
```

The check does not write files or run subprocesses. Its JSON object reports
`ok`, `needs_setup`, `would_write`, `would_update`, `client_transport_check`,
`diagnostics`, and `actions` so automation can decide whether to run the
applying command. `client_transport_check.status` is `unsupported_from_cli`
because a standalone CLI can verify static readiness but cannot prove that an
already-open codex or Claude Code stdio transport is live. `issuekit setup`
keeps its applying behavior, and `issuekit setup apply --json` is an explicit
alias for that path.

The repo scaffold writes `.mcp.json`, appends `.codex/config.toml` when needed,
and adds thin handoff references to `AGENTS.md` and `CLAUDE.md`. The generated
MCP entries run the global `issuekit-mcp` binary; they do not use `uv run`, so
they work outside the issuekit checkout. Launch codex or Claude Code from the
target repo root so the server resolves repo configuration.

When the MCP transport is live, the MCP `health` tool reports the server cwd,
issuekit version, resolved project, API URL presence, local worker presence,
and author guard state without mutating issue lifecycle state.

If an MCP client still exposes `mcp__issuekit` tools but every call fails with
`Transport closed`, tool discovery is stale. Until the client transport is
reloaded, use the equivalent CLI commands for read-only inspection and proposal
inbox work:

```powershell
issuekit protocol --role author
issuekit incoming --json
issuekit info --json
```

Repo-local `.env` files are treated as trusted repository input only for
`ISSUEKIT_*` keys. Sensitive API settings loaded from `.env` are announced on
stderr so credential redirection is visible.

For local development, install the optional MCP group and start the stdio server
from a checkout with:

```powershell
uv run --group mcp issuekit-mcp
```

## Handoff protocol

The role-based author, implementer, and reviewer protocol is centralized in
issuekit:

```powershell
issuekit protocol
issuekit protocol --agent codex
issuekit protocol --agent claude
issuekit protocol --agent kimi
issuekit protocol --role author
issuekit protocol --role implementer
issuekit protocol --role reviewer
```

The MCP server exposes the same text as its instructions and through the
`get_protocol` tool. Consuming repos should reference this command instead of
copying the steps.

## Commands

| Command | Purpose |
|---------|---------|
| `issuekit info [--json]` | Show API tracker status. |
| `issuekit validate` | Check API connectivity and issue response shape. |
| `issuekit migrate-to-api [--dry-run]` | Import legacy `docs/issues/{active,completed}` files into the API backend. |
| `issuekit migrate-proposals-to-api [--dry-run]` | Import legacy proposal inbox files into the API backend. |
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
| `issuekit check-encoding [--json]` | Check tracked source files for leading BOM bytes and likely mojibake. |
| `issuekit protocol [--agent codex\|claude]` | Print the canonical handoff protocol. |
| `issuekit init [--with-mcp]` | Install tracker templates, encoding hooks, and optional MCP handoff scaffolding. |
| `issuekit setup [--force] [--json]` | Run per-repo MCP handoff scaffolding and setup diagnostics. |
| `issuekit setup check --json` | Check setup state without writing files. |
| `issuekit dev-tool install-editable [--repo <path>] [--no-stop] [--json]` | Windows developer command to install this checkout as the global editable tool with the MCP extra. |
| `issuekit dev-tool reinstall [--repo <path>] [--no-stop] [--json]` | Windows developer recovery command to reinstall the global tool from an absolute checkout path. |
| `issuekit dev-tool reload-mcp [--json]` | Stop only running `issuekit-mcp.exe` processes; MCP clients own respawn and stdio reconnection. |
| `issuekit add` / `issuekit register` | Register this git repo namespace and this checkout's worker (auto-derives repo and worker ids, with machine metadata, and publishes the configured API project). |
| `issuekit workers [--repo-id <id>] [--project <name>] [--json]` | List registered workers and their repo-level roles across projects. |
| `issuekit workers remove <worker.repo[@machine]\|machine/repo/worker> [--force] [--json]` | Remove a registered worker after checking for implementing issues. |
| `issuekit workers prune [--stale-after-sec <n>] [--dry-run] [--json]` | Remove stale workers that hold no implementing issue and are not targeted by directed work. |
| `issuekit repos remove <repo> [--json]` | Remove a repo catalog entry; the API refuses entries that still have references. |
| `issuekit add-ref <name> --path <repo> [--scope local\|workspace]` | Register an optional local project alias. |
| `issuekit list-refs` | List effective local project aliases and their source. |
| `issuekit propose --to <project> --title "..."` | Send a proposal to a project API inbox. |
| `issuekit incoming [--json]` | List inbound API proposals. |
| `issuekit outgoing --to <project> [--id <id>] [--status <status>]` | List proposals this project sent to a target project's inbox (read-only, self-scoped). |
| `issuekit adopt <proposal-id> [--json]` | Adopt an incoming API proposal as a local issue and print the created API issue id. |
| `issuekit discard <proposal-id>` | Discard an incoming API proposal. |

Legacy file parsing is used only by `migrate-to-api`,
`migrate-proposals-to-api`, and the explicit filesystem issue-store escape
hatch.

## Cross-Project Proposals

Related projects exchange suggestions through API proposal inboxes. Proposals
are not workflow items until they are adopted, so the API-backed issue queue is
separate from proposal triage.

Use `issuekit author` only for work that originates in and belongs to the
current project. If you are acting in project A and discover that the required
change belongs to project B, stay in project A and send a proposal:

```powershell
issuekit propose --to project-b --title "Short proposal title" --body-file proposal.md
```

Do not `cd` into project B and run `issuekit author`; that bypasses B's proposal
triage and makes the issue look locally originated. When `author` sees related
project context that looks cross-project, it stops before creating the issue and
prints the proposal command template. Pass `--direct-local-author` only when the
work is deliberately local despite mentioning a related project.

If a direct target-project issue was created by mistake, recover without editing
tracker metadata directly: send the proposal from the origin project, then close
the mistaken direct issue in the target project as superseded:

```powershell
issuekit complete <direct-issue-id> --force --summary "Superseded by proposal <proposal-ref>" --verification "Recovery bookkeeping only."
```

`--to` takes a registered target API project key, not an arbitrary alias. A
project becomes visible to other repos after that project runs `issuekit add` or
`issuekit register` against the API, or otherwise pushes a project profile.
If the API exposes its project catalog, issuekit rejects unknown targets before
creating a proposal. If the connected API predates project catalog support,
proposal writes continue and issuekit reports that the target could not be
validated.

The old workspace ref registry is kept only as an optional local alias map for
operators who want to remember sibling project names. Workspace/local refs can
remain convenient aliases, but proposal delivery resolves to registered API
project keys and no longer resolves a target repo path or writes files. For a
set of sibling repos, place one `issuekit.workspace.toml` above them:

```toml
[projects]
basekit = "basekit"
fast-domain = "fast-domain"
issuekit = "issuekit"
mine-py = "mine-py"
py_cr_wrapper = "py_cr_wrapper"
repom = "repom"
mine-js-monorepo = "mine-js-monorepo"
infra-toolkit = "infra-toolkit"
```

`issuekit` discovers the nearest `issuekit.workspace.toml` by walking up from
the current directory. `ISSUEKIT_WORKSPACE` can point to an explicit workspace
file and overrides discovery. Relative `[projects]` paths resolve against the
workspace file's directory, so sibling entries like `fast-domain = "fast-domain"`
survive moving the whole workspace. Absolute paths are allowed for out-of-tree
repos.

Each repo can still use gitignored `issuekit.local.toml` with a `[refs]` table
for private refs or overrides. Effective refs are loaded as workspace projects,
then local refs; local entries win on name conflicts.

Do not assume a repo remote name is a valid proposal destination. For example,
if a local alias such as `mine-dashboard` points at an old service name but the
target project is registered as `dashboard`, `issuekit propose --to mine-dashboard ...`
is rejected on catalog-aware APIs instead of creating a
proposal in an unwatched inbox.

Manage refs with:

```powershell
issuekit add-ref fast-domain --path C:/abs/path/to/fast-domain
issuekit add-ref fast-domain --path ../fast-domain --scope workspace
issuekit list-refs
```

`add-ref` defaults to `--scope local`. `--scope workspace` writes to the
discovered workspace file; if none is found, create one explicitly or pass
`--path-to-workspace <file>`.

Send a proposal:

```powershell
issuekit propose --to fast-domain --title "Short proposal title" --body-file proposal.md
```

For multi-project changes, create or propose the upstream project that owns the
first required API or contract change before sending downstream consumer
proposals. Attach the upstream reference with `--depends-on`:

```powershell
issuekit propose --to mine-js-monorepo --title "Use new API" --body-file proposal.md --depends-on mine-py#proposal:123
```

Accepted dependency refs are `project#N`, `project#issue:N`, and
`project#proposal:N`. Bare `project#N` refs are accepted for compatibility, but
can be shadowed when an issue and a proposal share the same number. Prefer
`project#proposal:N` when depending on a not-yet-adopted proposal. Structured
body lines such as `Depends-On: mine-py#proposal:123` are also recognized.
If the proposal body appears to depend on a third project but no upstream
reference is supplied, issuekit prints an advisory warning and still sends the
proposal.

Triage inbound proposals:

```powershell
issuekit incoming
issuekit adopt 42
issuekit discard 43
```

To reply after implementing an adopted issue, run:

```powershell
issuekit propose --reply 42 --title "Implemented fast-domain support" --body-file reply.md
```

By default `--reply` derives the destination project from the recorded `origin`
value before `#`. Pass `--to <project>` with `--reply` to override that
destination.

Proposal de-duplication is keyed by the full `origin`, including `@commit`.
Re-sending the same source issue after a new commit creates a new proposal.

## Configuration

Python repositories can configure issuekit in `pyproject.toml`:

```toml
[tool.issuekit]
api_url = "https://mine.example"
project = "issuekit"
assignees = ["codex", "claude"]
disabled_agents = ["kimi"]
stages = ["planned", "todo", "implementing", "review", "changes_requested", "done"]
default_reviewer = "auto"
require_distinct_reviewer = true
work_branch = "main"
```

Non-Python repositories can use a standalone `issuekit.toml` at the repo root
with the same keys at the top level:

```toml
api_url = "https://mine.example"
project = "issuekit"
assignees = ["codex", "claude"]
disabled_agents = ["kimi"]
stages = ["planned", "todo", "implementing", "review", "changes_requested", "done"]
default_reviewer = "auto"
require_distinct_reviewer = true
work_branch = "main"
```

The mine-py server owns issue ids and reviewer policy. When `api_url` is set,
issuekit always treats review handoff as `default_reviewer = "auto"` and
`require_distinct_reviewer = true` for local decisions, regardless of local
reviewer-policy keys:

```toml
[tool.issuekit]
api_url = "https://mine.example"
project = "issuekit"
default_reviewer = "auto"
require_distinct_reviewer = true
```

Legacy `docs/issues/` files are read only by the migration commands. Runtime
issue lifecycle commands use the API store.

Use `disabled_agents` to remove an agent from claim, review, router, triage, and
`implement --agent` candidacy without deleting its run configuration. The key is
a deny-list; omit it or set `disabled_agents = []` to enable all configured
agents. `issuekit.local.toml` accepts the same key as a machine-local override,
so a checkout can disable an unavailable binary without changing committed
repo policy. When `assignees` is omitted, issuekit defaults it to the enabled
agent names; an explicit `assignees` list still defines the assignment pool.

Set `work_branch` to pin handoff lifecycle work to one branch. When set,
`claim`, `implement`, `serve`, and `submit-review` fail before mutating issue
state if the checkout is on another branch or the branch cannot be determined.
The guard never switches branches. Omit `work_branch` or set it to an empty
string to disable the guard, which is the default. The config shape is intended
to grow later to an allowed branch list or glob such as
`allowed_branches = ["main", "release/*"]`; today it is a single branch string.

At startup, issuekit also reads a repo-local `.env` file from the current repo
root and loads values such as `ISSUEKIT_API_URL`, `ISSUEKIT_API_USER`,
`ISSUEKIT_API_PASSWORD`, `ISSUEKIT_API_TOKEN`, `ISSUEKIT_TOKEN_CACHE`, and
`ISSUEKIT_PROJECT`. Existing process environment variables are not overwritten.
Overall precedence is process environment, then `.env`, then `[tool.issuekit]`
or `issuekit.toml`, then built-in defaults. Set
`ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` to skip only the local author-session STOP
guard enforcement across checkouts; unset or truthy values keep the default
enforcement behavior.

When `ISSUEKIT_API_URL` uses plain `http://` for a non-loopback host, issuekit
prints a stderr warning because credentials and bearer tokens are sent without
transport encryption. For a temporary trusted endpoint, set
`ISSUEKIT_ALLOW_INSECURE=1` in the process environment or repo-local `.env` to
suppress that warning.

When both files exist, `[tool.issuekit]` in `pyproject.toml` takes precedence.
If `pyproject.toml` exists without `[tool.issuekit]`, issuekit falls back to
`issuekit.toml`.

Run `issuekit add` / `issuekit register` from a git-managed checkout. The
command registers the repo issue namespace and a worker for this checkout in one
step. It derives `repo_id` and the canonical repo URL from `remote.origin.url`;
in a git checkout with no origin, pass `--repo-id <repository-id>` explicitly.
It refuses non-git directories. The worker name defaults to the checkout
directory basename and is displayed with the repo as `worker.repo`. `machine_id`
is stored as worker metadata, and same-named workers on different machines can
be addressed individually with the machine-qualified `worker.repo@machine` form
(see Directed Addressing). `project` remains the API issue/proposal namespace, so an explicitly
configured `project` is not overwritten by a remote name or `--repo-id`.

A repo can advertise its role so agents in other projects recognize peers when
choosing proposal or negotiation targets. Set `repo_description`,
`repo_metadata`, `worker_metadata`, `worker_role` (max 80 chars), and optional
`worker_description` (max 500 chars) in shared config, or pass
`--repo-description`, `--repo-metadata KEY=VALUE`, and
`--worker-metadata KEY=VALUE` to `issuekit add`. `issuekit add` and
`issuekit serve` send them to the backend, and `issuekit workers` lists the
catalog:

```toml
[tool.issuekit]
worker_role = "api-server"
worker_description = "Hosts the mine-py issue API and issuekit backend."
repo_description = "Issue API and issuekit backend."

[tool.issuekit.repo_metadata]
domain = "api"

[tool.issuekit.worker_metadata]
queue = "default"
```

Set `worker_accept_directed = true` only for production checkouts that are
intended to receive work addressed specifically to `worker.repo`. The default
is false, so a checkout participates only in the repo pool unless the backend
already trusts it for directed work. Combine this with target-owned intake
policy such as `[triage].trusted_origins` when only selected origin projects
should be auto-adopted into directed or blocking work.

Directed proposal checks are also worker-pull based. Run
`issuekit serve --agent <agent> --proposal-checks` from a registered checkout
to poll pending checks addressed to that worker and answer them automatically.
The loop uses `--interval` for idle polling, `--timeout-sec` for each read-only
agent evaluation, `--once` for a single cycle, and `--proposal-check-limit` for
the maximum checks evaluated per cycle. Transient API failures are logged and
retried with the same capped backoff used by the issue and review serve loops.

## Registry Maintenance

Use `issuekit workers remove <worker.repo>` to delete a known stale checkout
registration. The command accepts the current dotted key, the machine-qualified
`worker.repo@machine` address, and the legacy `machine/repo/worker` id, prints
the worker status, `last_seen`, and any implementing issue it found, and refuses
to delete an implementing holder unless `--force` is passed.

Use `issuekit workers prune --dry-run` to review cleanup candidates before
deleting anything. Prune only offers workers whose `last_seen` heartbeat is
older than `--stale-after-sec`, that hold no implementing issue, and that are
not the `target_worker` of directed work. Without `--dry-run`, the command asks
you to type the candidate count before it deletes the workers.
`last_seen` only refreshes while `serve` or the worker heartbeat is running, so
quiet but live checkouts can look stale; start with `--dry-run` and use a
generous `--stale-after-sec` before deleting.

If prune skips a stale worker because an issue is still directed to it, use
`issuekit readdress <id>` first to return that issue to the repo pool. If prune
skips a stale worker because it holds an implementing claim, inspect
`issuekit orphans` and use `issuekit reclaim <id>` when the claim is truly
orphaned.

Use `issuekit repos remove <repo>` only after worker cleanup. Catalog-aware API
servers return a conflict when a repo still has worker, issue, proposal, or
other references, and issuekit prints the reference counts returned by the API.

## Directed Addressing

Issuekit keeps three axes separate:

- `repo` / `project`: the API issue or proposal namespace, such as `mine-py`.
- `worker`: a registered checkout inside that repo, displayed as
  `worker.repo`, such as `prod.mine-py`.
- `agent` / `assignee`: the model or human role that implements or reviews
  work, such as `codex` or `claude`.

Most work should target the repo pool. For example, `issuekit propose --to
mine-py ...` lets any eligible worker registered for `mine-py` claim the
resulting work. When a target repo has opted a checkout into directed work, use
`worker.repo` to address that one checkout:

```console
$ issuekit propose --to prod.mine-py --title "Patch production profile" --body "..."
```

The same worker name can exist on several machines (for example a
provisioning-created devenv `alpha.mine-py` on both `pike3` and `main1`). To
address exactly one of them, append the machine id in the canonical
machine-qualified form `worker.repo@machine`:

```console
$ issuekit propose --to alpha.mine-py@pike3 --title "Patch pike3 profile" --body "..."
```

`issuekit workers` prints each worker's machine-qualified address so callers
know the exact string to direct to. A machine-qualified target only matches a
claiming worker on that machine, while the bare `worker.repo` form stays
machine-agnostic; the API rejects a bare directed target as ambiguous when the
same worker name is registered on multiple machines.

The dotted form is client-side sugar. Issuekit validates each token, sends the
repo/project separately from the worker name, and claim requests include the
local machine-qualified `worker.repo@machine` key so the API can hide work
directed to other workers or other machines.

Built-in agent configs can be patched by name. A table such as
`[tool.issuekit.agents.codex]` overlays only the keys it specifies and leaves
other built-in agents unchanged. For standalone `issuekit.toml`, use the same
table without the `tool.issuekit` prefix:

```toml
[tool.issuekit.agents.codex]
approval_flag = "--sandbox"
approval_value = "danger-full-access"
```

`default_reviewer` controls where MCP and CLI review handoffs go when no
reviewer is specified. It must be one of the configured `assignees`, or `auto`.
With `auto`, issuekit keeps the current review assignee when possible and
otherwise uses a stable configured assignee. When `require_distinct_reviewer` is
true, `auto` chooses an assignee that differs from the issue implementer and
same-name review is rejected.

## Orphaned Claim Detection

When an implementer session dies mid-turn it can leave an issue stuck at
`stage=implementing` with an `assignee` still set. Because the assignee is
populated, the pull-based pool never re-offers it, so no idle agent picks it
up and the issue silently stalls.

`issuekit orphans` surfaces these without out-of-band forensics. An implementer
claim records which worker checkout (`worker.repo`, with legacy
`machine/repo/worker` keys still recognized) holds the issue, and the worker
registry tracks each live checkout's `last_seen` heartbeat. The
command cross-references the two and flags an implementing issue when either:

- `no_worker`: no registered worker matches the claim's worker key, so the
  holder is gone; or
- `expired_heartbeat`: a matching worker exists but has not sent a heartbeat
  for at least `--stale-after-sec` seconds (default 300).

Directed but unclaimed work is also reported when its `target_worker` is gone
or stale, using `directed_no_worker` or `directed_expired_heartbeat`. These
issues are not implementing claims, but they will not return to the repo pool
until the directed target is cleared.

```console
$ issuekit orphans
Orphaned or stale implementing claims: 1
- #168: ... [assignee=claude worker=issuekit.issuekit] (stale: no heartbeat since 2026-07-03T01:32:30Z)
```

The `last_seen` heartbeat is refreshed by the `issuekit serve` worker loop (and
on `issuekit add`), not by a one-shot `issuekit claim`/`issuekit implement`.
A long-running implementer run through `serve` heartbeats every 60s and is not
flagged; a manual one-shot implementer that holds a claim without running
`serve` may show as `expired_heartbeat`.

Use `issuekit reclaim <id>` to return a listed stale claim to the implement
pool. The command re-checks `orphans` before calling the API and passes the
detected worker as a race guard, so a resumed holder is not overwritten. Use
`--force` only for human emergency recovery when the staleness check should be
skipped. `--force` still sends the worker that held the issue when issuekit read
it, so it skips only the staleness check. If that worker resumes or another
worker takes the claim between the read and the reclaim request, the API returns
`race_lost` instead of overwriting the current holder. This keeps the emergency
path optimistic-concurrency safe; there is intentionally no unconditional
override flag that sends `expected_worker=None`.

Use `issuekit readdress <id>` to clear a directed `target_worker` and return
that issue to the repo pool. The command sends the target worker it observed as
a race guard, so if the API sees a different target by the time it handles the
request it rejects the update instead of clearing newer directed work.

## Separation-of-Duties Guards

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

## Development

This repo dogfoods issuekit. Implementation tasks and cross-project proposals
live in the configured API project.

Windows developer global-tool workflow:

```powershell
uv run issuekit dev-tool install-editable
uv run issuekit dev-tool reload-mcp
uv run issuekit dev-tool reinstall
```

Pass `--json` to any `dev-tool` action for automation. The JSON payload includes
`ok`, `actions`, `stopped_processes`, `commands`, `diagnostics`, and
`client_transport_check`. `reload-mcp` also includes `mcp_process_check` with
the stopped process count.
