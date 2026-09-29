# Configuration

## Config files

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
gate_halfwidth_kana = true
check_encoding_exclude = ["packages/*/src/generated/**"]
worker_heartbeat_interval_sec = 60.0
send_agent_runtime = true
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
gate_halfwidth_kana = true
check_encoding_exclude = ["packages/*/src/generated/**"]
worker_heartbeat_interval_sec = 60.0
```

When both files exist, `[tool.issuekit]` in `pyproject.toml` wins and
`issuekit.toml` is ignored entirely; the two files are not merged. If
`pyproject.toml` exists without `[tool.issuekit]`, issuekit falls back to
`issuekit.toml`.

Repository config is strict about values but not about names: an invalid value
fails config loading, while an unknown or misspelled key is silently ignored.
Run `issuekit info` to see which repository
config source and machine config file were loaded, along with the effective
agent settings.

`project` names the API issue and proposal namespace. When it is unset, it
defaults to the registered worker's `repo_id` from `issuekit.local.toml`, then
to `issuekit`. `api_timeout` is the HTTP timeout in seconds for tracker API
calls and defaults to `30.0`. `stages` lists the stage names issuekit accepts
locally: lifecycle commands fail with `Unknown stage: <name>` when their target
stage (`implementing`, `review`, or `changes_requested`) is missing, and a
stage filter such as `issuekit queue --stage` must name a listed stage. Adding
a name does not create a workflow stage, so keep the default list.

## Machine config

Machine-wide defaults can be stored in `~/.config/issuekit/config.toml` on both
platforms (on Windows, `%USERPROFILE%\.config\issuekit\config.toml`).
`XDG_CONFIG_HOME` is honored on both platforms. Set `ISSUEKIT_CONFIG` to use an
explicit file, or set it to an empty string to disable machine config loading.
Existing Windows users with a config under `%APPDATA%` should move it to
`%USERPROFILE%\.config\issuekit\` or set `ISSUEKIT_CONFIG`. Machine config has
lower precedence than repository config and cannot define `worker`; checkout
registration belongs in `issuekit.local.toml`. Agent tables merge by key between
machine and repository layers, one level deep: a repository `[agents.codex]`
table overrides individual keys such as `model`, but its `roles` and
`model_prompts` sub-tables replace the machine ones whole. Other values,
including the `[triage]`, `[router]`, and `[agent_roles]` tables, are replaced
whole by the higher-precedence layer. Repository identity settings such as
`project`, `work_branch`, `issues_dir`, and `profile_*` normally belong in
repository config.

Because one machine config serves every checkout, issuekit is lenient with it:
unknown keys at any level, `[agent_roles]` entries with an invalid role, and an
invalid `[triage] default_priority` are dropped with a warning instead of
failing. Other invalid values fail as they do in repository config.

Set `default_implementer` in machine config when one agent is the usual worker
on that machine. Commands and MCP tools that omit an implementer use it before
falling back to a single enabled assignee; repository config can override it.

Use `[agent_roles]` in machine config to select the protocol each agent sees.
For example, when Claude is the implementer on that machine:

```toml
[agent_roles]
claude = "implementer"
```

Valid roles are `author`, `implementer`, `pm`, `reviewer`, and `triage`. Each
agent has exactly one default role in this table; it cannot be configured
with two. When one agent serves more than one role, request the non-default
protocol explicitly with `issuekit protocol --role <role>` or
`get_protocol(role=...)`. The explicit role always wins over the agent default,
so prefer it whenever the role is known, including when `[agent_roles]` is
unset. Run `issuekit info` to inspect the effective mapping under `Agent roles`,
including built-in defaults, before relying on `--agent`.

`[agent_roles]` selects protocol text only. Role-scoped model overlays such as
`[agents.<name>.roles.<role>]` are resolved by the launch site instead:
`issuekit implement` and implement-mode `serve` pass `implementer`;
`issuekit review` and `serve --review` pass `reviewer`; `issuekit request`
passes `router`; and `issuekit proposal-checks`, `serve --proposal-checks`,
and the triage author (`issuekit triage --once`, or `serve --triage` with
`[triage] author_agent`) pass `triage`. `issuekit negotiate` passes no role,
so role overlays never apply to negotiation runs; use the agent defaults or
per-run `--model` and `--reasoning-effort` there. Model and effort selection
is therefore unaffected by `[agent_roles]`.

## Environment and precedence

At startup, issuekit also reads a repo-local `.env` file from the current repo
root and loads values such as `ISSUEKIT_API_URL`, `ISSUEKIT_API_USER`,
`ISSUEKIT_API_PASSWORD`, `ISSUEKIT_API_TOKEN`, `ISSUEKIT_TOKEN_CACHE`, and
`ISSUEKIT_PROJECT`. Only keys that start with `ISSUEKIT_` are loaded; other
entries are ignored. Existing process environment variables are not
overwritten.

Overall precedence, highest first:

1. Per-run CLI flags.
2. Process environment, then `.env`. Only three variables override config
   keys: `ISSUEKIT_API_URL` (`api_url`), `ISSUEKIT_PROJECT` (`project`), and
   `ISSUEKIT_API_TIMEOUT` (`api_timeout`). The other `ISSUEKIT_*` variables
   are credentials, paths, or switches with no config-file key.
3. `issuekit.local.toml`, for `worker` and `disabled_agents` only. Its
   `disabled_agents` list replaces the repository list rather than adding to
   it.
4. `[tool.issuekit]` in `pyproject.toml`, or `issuekit.toml`.
5. Machine config.
6. Built-in defaults.

Set `ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` to skip the local author-session STOP
guard enforcement across checkouts. The same switch makes claims send
`allow_self_implement`, so the server author-implementer guard is relaxed too;
the two guards cannot be relaxed separately. Unset or truthy values keep the
default enforcement behavior.

When the effective `api_url`, whether it comes from config, `.env`, or
`ISSUEKIT_API_URL`, uses plain `http://` for a non-loopback host, issuekit
prints a stderr warning because credentials and bearer tokens are sent without
transport encryption. For a temporary trusted endpoint, set
`ISSUEKIT_ALLOW_INSECURE=1` in the process environment or repo-local `.env` to
suppress that warning.

## Reviewer and implementer policy

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

`default_reviewer` controls where MCP and CLI review handoffs go when no
reviewer is specified. It must be one of the configured `assignees`, or `auto`.
With `auto`, issuekit keeps the current review assignee when possible and
otherwise uses a stable configured assignee. When `require_distinct_reviewer` is
true, `auto` chooses an assignee that differs from the issue implementer and
same-name review is rejected.

Without `api_url`, the local defaults are `default_reviewer = "claude"` and
`require_distinct_reviewer = false`. Because `default_reviewer` must name an
enabled assignee, a local config that disables Claude or leaves it out of
`assignees` must also set `default_reviewer`; otherwise config loading fails
with `default_reviewer references disabled agent: claude` or
`Unknown default_reviewer: claude`.

`default_implementer` controls which configured assignee MCP and CLI
implementation commands use when no implementer is specified. It must be one
of the configured `assignees`; leave it empty to require an explicit choice
when more than one enabled assignee is available.

## Enabling and disabling agents

Use `disabled_agents` to remove an agent from claim, review, router, triage, and
`implement --agent` candidacy without deleting its run configuration. The key is
a deny-list; omit it or set `disabled_agents = []` to enable all configured
agents. `issuekit.local.toml` accepts the same key as a machine-local override,
so a checkout can disable an unavailable binary without changing committed
repo policy. When `assignees` is omitted, issuekit defaults it to the enabled
agent names; an explicit `assignees` list still defines the assignment pool.

A setting that names a disabled agent fails config loading with
`<key> references disabled agent: <name>`. This applies to `default_reviewer`,
`default_implementer`, `[router] agent`, and `[triage] author_agent`, so
update those settings when you disable the agent they name.

## Agent overlays

Built-in agent configs can be patched by name. A table such as
`[tool.issuekit.agents.codex]` overlays only the keys it specifies and leaves
other built-in agents unchanged. For standalone `issuekit.toml`, use the same
table without the `tool.issuekit` prefix:

```toml
[tool.issuekit.agents.codex]
approval_flag = "--full-auto"
model = "gpt-6-sol"

[tool.issuekit.agents.codex.model_prompts]
"gpt-6-sol" = "Follow the gpt-6-sol project guidance."
```

A table with a new name, such as `[tool.issuekit.agents.gemini]`, defines a
custom agent that starts with no flags and `binary` set to the table name.
Agent tables accept these keys:

| Key | Meaning |
|-----|---------|
| `binary` | Executable looked up on `PATH`; defaults to the table name. |
| `known_paths` | Fallback executable paths tried after `PATH`; `~` is expanded. |
| `headless_argv` | Arguments placed before the prompt, such as `["exec"]` or `["-p"]`. |
| `approval_flag`, `approval_value` | Permission flag and its optional value. |
| `output_format_flag`, `output_format` | Output-format flag and value; the flag is emitted only when both are set. |
| `model_flag`, `model` | Model flag and default model; without `model_flag`, no model is passed. |
| `reasoning_effort`, `effort_argv` | Default effort and its argv template using `{value}`. |
| `speed`, `speed_argv` | Boolean switch and the literal arguments it emits. |
| `resumable`, `session_flag`, `resume_flag` | Session support; see below. |
| `prompt_suffix`, `model_prompts` | Text appended to every prompt, and per-model text. |
| `adapter` | Marker for a custom adapter class; only `kimi` is built in, and an unknown marker fails with `Unknown adapter`. |
| `runtime`, `app_server_argv`, `lease_ttl_seconds` | Runtime selection; see the App Server paragraphs below. |
| `mojibake_gate`, `diff_shape_warn_deletions` | Submit-time policy; see [Encoding checks](#encoding-checks). |
| `roles` | Per-role `model` and `reasoning_effort` overlays; see below. |

For the runtime boundary and how to add a config-only or custom agent adapter,
see [`issuekit/agentrun/README.md`](../../issuekit/agentrun/README.md).

Session flags come in a pair. `session_flag` starts a new agent session under a
caller-chosen id, and `resume_flag` continues a session that issuekit already
started; both require `resumable = true`. Negotiation uses the pair to keep one
session per side across the rounds of a run, so an agent configured with only
`session_flag` gets a fresh session per round as before. The built-in Claude
config sets `session_flag = "--session-id"` and `resume_flag = "--resume"`.

By default, issuekit runs Codex without a sandbox and relies on the repository
worktree plus the review gate. Projects that require the strict sandbox can use
the override above, or set `approval_flag = "--sandbox"` and
`approval_value = "workspace-write"`. These overrides apply only to the default
exec runtime; the App Server runtime below ignores them.

Codex implementation runs use `codex exec` by default. API-backed projects can
opt into issue-owned App Server attempts for Codex implementer runs:

```toml
[tool.issuekit.agents.codex]
runtime = "codex_app_server"
lease_ttl_seconds = 60
```

This mode requires a registered worker and provider support for the
`agent-sessions` v1 routes. It launches `codex app-server` over local JSONL
stdio only. `app_server_argv` replaces the default `["app-server"]` rather than
appending to it, so it must start with `app-server`, and `--listen` accepts
only `stdio://`. `lease_ttl_seconds` defaults to `60` and must be between 15
and 300. `runtime` accepts `exec` (the default) or `codex_app_server`, and
`codex_app_server` is accepted only in the `codex` agent table. The worker
heartbeats below half the configured lease TTL, stores its lease token only in
memory, journals commands before local side effects, uploads bounded redacted
events, stops on fencing or claim loss, and seals the runtime before the
existing submit-for-review workflow. A provider
without the routes returns a clear unsupported-runtime error; issuekit does not
silently fall back because the mode is explicit. App Server is Codex-only and
implementer-only in this version. Its threads always start with approval policy
`never` and the `dangerFullAccess` sandbox, and it reads only `binary`,
`known_paths`, `lease_ttl_seconds`, `app_server_argv`, `model`,
`reasoning_effort`, `prompt_suffix`, and `model_prompts` from the agent
config: `approval_flag`, `approval_value`,
`headless_argv`, `speed`, and `speed_argv` have no effect in this mode. The
`issuekit implement --follow` heartbeat
applies only to the default exec runtime; it polls `git status` read-only without
an index lock, so it is safe for issues that rewrite the checkout.

Both runtimes send the plan pointer plus the agent's `prompt_suffix` and any
matching `model_prompts` entry. For the implementer report destination, the exec
prompt names `ISSUEKIT_IMPLEMENTER_REPORT_FILE`; the App Server prompt replaces
that instruction with the concrete path because its session does not receive
the exec environment. App Server attempts also record token usage. Codex reports
it through `thread/tokenUsage/updated`
notifications, which issuekit uploads as `turn_progress` events carrying a
`usage` payload with `last` and `total` breakdowns. The cumulative thread total
is repeated in the final `runtime_stopped` event, in the run's
`<run_id>.out.log` JSON, and as `usage_*` entries on the run result, so App
Server and exec runs can be compared without reading the raw agent log.

The built-in Claude config bypasses permissions so headless implementer runs
can execute shell commands unattended. Stricter projects can restore the old
behavior with `[agents.claude] approval_value = "acceptEdits"` in repo or
machine config.

That config also sets `output_format = "json"`, so Claude returns a result
envelope instead of bare text. An agent configured with `output_format = "json"`
has its envelope unwrapped by the adapter: `stdout` becomes the agent's own
reply, exactly as under `"text"`, and the run result gains `session_id`,
`cost_usd`, `usage_*`, `is_error`, `terminal_reason`, and `num_turns` entries
when the envelope carries them, plus `failure_reason` (the envelope's result
text) when `is_error` is true. The unwrapped run log keeps the full
envelope, and an agent that dies before emitting one keeps its raw stdout, so
crash diagnostics are unchanged. Set `output_format = "text"` to opt out; the
recorded metrics are then unavailable.

Agent-launching commands accept pass-through `--model <model-id>` and
`--reasoning-effort <value>` overrides, including `implement`, `review`,
`negotiate`, `request`, `serve`, `triage`, and `proposal-checks`. Issuekit does not
restrict model ids; the selected agent CLI validates them. The `model` and
`reasoning_effort` agent overlays set defaults, while `model_prompts` adds
prompt text for keys matching the resolved model id. A key matches exactly, or,
if it ends in `*`, as a prefix; when several prefixes match, the longest one
wins, and an exact match always beats a prefix match. This keeps guidance keyed
to a model family (for example `"claude-sonnet-5*"`) applying when the
resolved model id gains a date suffix or point revision. A prefix also matches
later model ids that extend it: `"claude-sonnet-5*"` matches
`claude-sonnet-5-5` as well as `claude-sonnet-5`, so use the exact key
`"claude-sonnet-5"` for guidance meant for one model only. Explicit per-run
values take precedence over configured defaults. A serve override applies to
every agent launched by that loop, so mixed-agent serve setups should configure
`model` and `reasoning_effort` in each agent's overlay instead.

One project reported a recurring implementer failure shape for the
`claude-sonnet-5` model family: correct mechanisms applied at one level too
coarse a granularity (staleness guards keyed by entity instead of per-request,
in-flight request sharing that a forced refresh should have bypassed, a limit
enforced at the outer boundary instead of per-resource) and reset paths that
handled every branch except an early return. The following `model_prompts`
fragment is a starting point, not a shipped default; adapt it per project. Its
`"claude-sonnet-5*"` key also applies to `claude-sonnet-5-5`; change it to
`"claude-sonnet-5"` if the guidance should not follow newer Sonnet models:

```toml
[tool.issuekit.agents.claude.model_prompts]
"claude-sonnet-5*" = """
When adding a staleness guard, state what one request is and confirm the key
changes per request, not per selected entity.
When a request can be superseded, check whether a forced or retried call must
bypass any in-flight sharing.
When a limit is per-resource, apply it where the resource is known, not at the
outermost boundary.
When adding an is-current check around state writes, enumerate every
early-return and reset branch and clear loading and error state there too.
When deleting a test file, first check whether its cases cover code that
survives the deletion, and move them rather than dropping them.
"""
```

An agent can also set model and reasoning-effort defaults for the
`implementer`, `reviewer`, `router`, or `triage` role:

```toml
[tool.issuekit.agents.claude]
model = "claude-sonnet-5"

[tool.issuekit.agents.claude.roles.reviewer]
model = "claude-opus-4-8"
```

Role overlays accept only `model` and `reasoning_effort`, and take precedence
over the agent default but not explicit per-run values. This lets one agent
name use different settings for implementation and review within one `serve`
loop. An agent must define `effort_argv` to support `reasoning_effort`; the
built-in Codex adapter uses `("-c", "model_reasoning_effort={value}")` and the
built-in Claude adapter uses `("--effort", "{value}")`. The `speed` setting is
a boolean switch; `true` emits the agent's `speed_argv` entries verbatim, while
`false` or an absent setting emits nothing. The built-in templates carry each
CLI's wire value: Codex uses `("-c", "service_tier=priority")`, and Claude uses
`("--settings", '{"fastMode": true}')`. To select a different Codex service
tier such as `flex`, override `speed_argv` with
`["-c", "service_tier=flex"]`. Current issuekit rejects the old string form:
`speed = "priority"` fails config loading with
`Invalid boolean config value: priority`, so replace it with `speed = true`.
An older pinned issuekit that reads `speed = true` may stringify it and emit
`service_tier=True`, so upgrade those pins at the same time. Setting
`speed = true` on an agent without `speed_argv`, such as the built-in Kimi
config or a custom agent, fails when the agent is launched.

By default, agent-launched implementation and review transitions report the
effective model and reasoning effort to mine-py. Set `send_agent_runtime = false`
when using a mine-py deployment older than mine-py#579: that server rejects the
additional fields with HTTP 422, so the transition fails rather than omitting
the runtime data.

`worker_heartbeat_interval_sec` controls how often `issuekit serve` refreshes
its worker registry entry and defaults to `60.0`. The
`--heartbeat-interval <seconds>` serve option overrides it for one process.
Staleness windows used by `issuekit workers prune` and `issuekit orphans` must
be several heartbeat periods wide.

## Work branch guard

Set `work_branch` to pin handoff lifecycle work to one branch. When set,
`claim`, `implement`, `serve`, and `submit-review` fail before mutating issue
state if the checkout is on another branch or the branch cannot be determined.
The guard never switches branches. Omit `work_branch` or set it to an empty
string to disable the guard, which is the default. The config shape is intended
to grow later to an allowed branch list or glob such as
`allowed_branches = ["main", "release/*"]`; today it is a single branch string.

See [Separation-of-duties guards](separation-of-duties.md) for the full guard
table.

## Claim-sync guard

When `work_branch` is set, issuekit also checks that the checkout is clean
before `claim`, `implement`, or `serve` claims work. On the configured work
branch with an `origin` remote, it fetches that branch and fast-forwards the
checkout before claiming. `claim_sync` defaults to `true`; set it to `false`
to disable this guard. `claim_sync_interval_sec` defaults to `60`, must be zero
or greater, and limits how often a successful fetch runs for the same checkout
and branch. The throttle is kept in process memory, so it only takes effect in
long-lived processes such as `issuekit serve` or the MCP server; each one-shot
`claim` or `implement` command fetches again.

The guard does not run without `work_branch`. It blocks a dirty checkout, a
failed `git status`, or a failed fetch or fast-forward so the operator can fix
the checkout and retry. After inspecting a known-safe situation, such as
leftover work from a timed-out implement run, pass `--no-sync` to `claim`,
`implement`, or `serve` to deliberately skip this guard for that command; the
MCP `claim_next_task` tool accepts `no_sync` for the same purpose.

Claiming a specific issue with `claim --id` or `implement <id>` skips this
guard when the issue is at `changes_requested` and this worker implemented it,
so the worker can continue its own changes.

## Encoding checks

The agent submit mojibake gate checks half-width katakana by default. Run
`issuekit check-encoding --gate` to reproduce its verdict for the current
worktree. Set `gate_halfwidth_kana = false` only when touched generated files
legitimately contain half-width katakana; other encoding-artifact checks remain
enabled. The gate honors `check_encoding_exclude` for unconfirmed hits, so use
only narrow repo-relative path globs for generated trees or known-legitimate
text. Confirmed corruption still blocks submission in every path.

The gate is per-agent policy, set in the agent table next to a heavy-deletion
warning. It applies when issuekit launches the agent for implementation
(`issuekit implement` or `serve`), under either runtime:

- `mojibake_gate` (boolean) stops the run from submitting for review when
  lines the agent changed contain likely mojibake, and prints each hit.
- `diff_shape_warn_deletions` (integer) prints a
  `WARNING: heavy deletion diff detected` line for each changed file whose diff
  deletes more than that many lines. It only warns and does not block.

The built-in `codex` and `claude` configs set `mojibake_gate = true` and
`diff_shape_warn_deletions = 40`. Kimi and custom agents have the gate off and
no deletion warning unless their table sets these keys:

```toml
[tool.issuekit.agents.kimi]
mojibake_gate = true
diff_shape_warn_deletions = 40
```

For likely mojibake, `check-encoding` has three outcomes: confirmed candidates
are reported, unconfirmed candidates are suppressed but available through
`--show-unconfirmed-mojibake` and `unconfirmed_mojibake_hits`, and other text is
not a candidate. Unconfirmed means inconclusive, not proven legitimate. CI can
use `--fail-on-unconfirmed` to report and fail on unconfirmed candidates, but it
also fails on legitimate Japanese that is indistinguishable from lossy
corruption. Use it only with `check_encoding_exclude` entries for trees that
legitimately contain such text, or when the project has none.

Set `check_encoding_exclude` to a list of POSIX-style, repo-relative glob
patterns for generated paths. The exclusions suppress unconfirmed mojibake
candidates, but confirmed mojibake remains reported by `issuekit
check-encoding` and blocked by the agent submit gate in every path. They are a
whole-path filter for BOM, stray carriage-return, and CRLF checks. Use
repeatable `--exclude PATTERN` flags for one-off default scans. Gate mode uses
the configured exclusions exactly and cannot be combined with scan modifiers.

## Registration and repo metadata

Run `issuekit add` / `issuekit register` from a git-managed checkout. The
command registers the repo issue namespace and a worker for this checkout in one
step. It derives `repo_id` and the canonical repo URL from `remote.origin.url`;
in a git checkout with no origin, pass `--repo-id <repository-id>` explicitly.
It refuses non-git directories. The worker name defaults to the checkout
directory basename and is displayed with the repo as `worker.repo`. `machine_id`
is stored as worker metadata, and same-named workers on different machines can
be addressed individually with the machine-qualified `worker.repo@machine` form
(see [Directed addressing](directed-addressing.md)). `project` remains the API
issue/proposal namespace, so an explicitly configured `project` is not
overwritten by a remote name or `--repo-id`.

A repo can advertise its role so agents in other projects recognize peers when
choosing proposal or negotiation targets. Set `repo_description` (max 500
chars), `repo_metadata`, `worker_metadata`, `worker_role` (max 80 chars), and
optional `worker_description` (max 500 chars) in shared config, or pass
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

A project can also publish a capability profile for the
[PM request router](pm-request.md). `profile_file` names the committed
markdown file, relative to the repo root, and defaults to `ISSUEKIT.md`; files
over 16 KiB are not pushed. `profile_summary` (max 500 chars) and
`profile_tags` (at most 20 tags) add short metadata. Each tag must be a
lowercase token of letters, digits, `_`, or `-` that starts with a letter or
digit and is at most 32 characters. issuekit pushes the profile with each
worker registration, from `issuekit add` and the `issuekit serve` heartbeat,
and skips the push when the file does not exist. `issuekit profile` shows the
local and stored profile.

```toml
[tool.issuekit]
profile_summary = "Issue API and issuekit backend."
profile_tags = ["api", "issue-tracker"]
```

Set `worker_accept_directed = true` only for production checkouts that are
intended to receive work addressed specifically to `worker.repo`. The default
is false, so a checkout participates only in the repo pool unless the backend
already trusts it for directed work. Combine this with target-owned intake
policy such as `[triage].trusted_origins` (see
[Proposal triage](#proposal-triage)) when only selected origin projects should
be auto-adopted into directed or blocking work.

## Proposal triage

The `[triage]` table (`[tool.issuekit.triage]` in `pyproject.toml`) is
target-owned policy for handling pending inbox proposals automatically:

```toml
[tool.issuekit.triage]
auto_adopt = true
trusted_origins = ["mine-py", "js-mine"]
default_priority = "medium"
require_blocking = false
max_adoptions_per_cycle = 5
author_agent = ""
```

`auto_adopt = true` makes every implement-mode `issuekit serve` poll run the
triage step, as if `--triage` were passed; it defaults to `false`. Either way,
only proposals that match the policy are handled, and the rest stay pending for
manual triage. A pending proposal matches when its origin project is listed in
`trusted_origins`, it is not part of a negotiation that is in progress, agreed,
or blocked, and, when `require_blocking = true`, it was sent with
`issuekit propose --blocking`. `trusted_origins` defaults to an empty list, so
nothing is adopted until origins are listed, even with `auto_adopt = true` or
`--triage`.

`default_priority` is the priority given to issues created from adopted
proposals: `high`, `medium` (the default), or `low`. Proposal checks that
approve and adopt a proposal use it too. `max_adoptions_per_cycle` must be at
least 1 and defaults to 5; it caps adoptions per serve poll.

Set `author_agent` to a configured, enabled agent to have that agent triage
each matching proposal instead of adopting it mechanically. The agent reads the
checkout without changing it and decides to adopt the proposal with an authored
spec, optionally replying to the origin project, to reply asking for
clarification, or to discard it. In this mode, `max_adoptions_per_cycle` caps
how many proposals the agent evaluates per cycle. `issuekit triage --once` runs
one such cycle and requires `author_agent`. See
[Cross-project proposals](cross-project-proposals.md) and
[Serve worker loop](serve.md) for the surrounding workflow.
