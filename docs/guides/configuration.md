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

Unknown config keys are often ignored, but not every typo is: a misspelled role
table such as `[agents.claude.roles.reviwer]` fails with
`Invalid agents.claude.roles role`. Value handling also varies by setting and
shape. For example, `check_encoding_exclude = 5` becomes an empty list, a
non-table `agents.x` is skipped, and `api_timeout <= 0` is accepted. Run
`issuekit info` to see which repository config source and machine config file
were loaded, along with the effective agent settings.

`project` names the API issue and proposal namespace. When it is unset, it
defaults to the registered worker's `repo_id` from `issuekit.local.toml`, then
to `issuekit`. `api_timeout` is the HTTP timeout in seconds for tracker API
calls and defaults to `30.0`. `stages` lists the stage names issuekit accepts
locally: lifecycle commands fail with `Unknown stage: <name>` when their target
stage (`implementing`, `review`, or `changes_requested`) is missing, and a
stage filter such as `issuekit queue --stage` must name a listed stage. Adding
a name does not create a workflow stage, so keep the default list.

`issues_dir` sets the directory for local issue documents and defaults to
`docs/issues`. `issuekit init` writes its issue-directory README there. During
an agent run, changes under this directory do not count as implementation
changes, and the submit-time mojibake scan excludes it.

## Machine config

Machine-wide defaults can be stored in `~/.config/issuekit/config.toml` on both
platforms (on Windows, `%USERPROFILE%\.config\issuekit\config.toml`).
`XDG_CONFIG_HOME` is honored on both platforms. Set `ISSUEKIT_CONFIG` to use an
explicit file, or set it to an empty string to disable machine config loading.
Existing Windows users with a config under `%APPDATA%` should move it to
`%USERPROFILE%\.config\issuekit\` or set `ISSUEKIT_CONFIG`. Machine config
cannot define `worker`; checkout registration belongs in
`issuekit.local.toml`. For settings that repository config may set, agent
tables merge by key between machine and repository layers, one level deep: a
repository `[agents.codex]` table overrides individual keys such as `model`,
but its `roles` and `model_prompts` sub-tables replace the machine ones whole.
Agent launch settings are machine-only and cannot be overridden by repository
config. Other values, including the `[triage]`, `[router]`, and `[agent_roles]`
tables, are replaced whole by the higher-precedence layer. Repository identity
settings such as `project`, `work_branch`, `issues_dir`, and `profile_*`
normally belong in repository config.

Because one machine config serves every checkout, issuekit is lenient with it:
unknown keys at any level, `[agent_roles]` entries with an invalid role, and an
invalid `[triage] default_priority` are dropped with a warning instead of
failing. Other invalid values fail as they do in repository config.

Set `default_implementer` in machine config when one agent is the usual worker
on that machine. Commands and MCP tools that omit an implementer use it before
falling back to a single enabled assignee; repository config can override it.

Machine config also accepts `trusted_api_origins`, a list of API origins that
repository `api_url` values may use, and `allow_insecure_api_url`, a boolean
that permits non-loopback `http://` API URLs on this machine. These two keys
are machine-only; repository config cannot set them. Origins are normalized to
`scheme://host[:port]`.

```toml
trusted_api_origins = ["https://mine.example"]
allow_insecure_api_url = false
```

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

`[agent_roles]` selects protocol text only. Role-scoped model and launch
overlays such as `[agents.<name>.roles.<role>]` are resolved by the launch site:
`issuekit implement` and implement-mode `serve` pass `implementer`;
`issuekit review` and `serve --review` pass `reviewer`; `issuekit request`
passes `router`; and `issuekit proposal-checks`, `serve --proposal-checks`,
and the triage author (`issuekit triage --once`, or `serve --triage` with
`[triage] author_agent`) pass `triage`. `issuekit negotiate` passes
`negotiation`, so role overlays also apply to both negotiation agents. Model
and effort selection is unaffected by `[agent_roles]`.

## Environment and precedence

At startup, issuekit also reads a repo-local `.env` file from the repository's
config root, regardless of the current directory. It accepts only
`ISSUEKIT_API_URL`, `ISSUEKIT_API_USER`, `ISSUEKIT_API_PASSWORD`,
`ISSUEKIT_API_TOKEN`, `ISSUEKIT_PROJECT`, and `ISSUEKIT_API_TIMEOUT`.
Other `ISSUEKIT_*` keys are ignored with a stderr notice that they must be set
in the process environment. Existing process environment variables are not
overwritten. Issuekit refuses to load `.env` when Git tracks it; untrack the
file with `git rm --cached .env` and keep it local.

Overall precedence, highest first:

1. Per-run CLI flags.
2. Process environment, then `.env`. `ISSUEKIT_API_URL` (`api_url`),
   `ISSUEKIT_PROJECT` (`project`), and `ISSUEKIT_API_TIMEOUT` (`api_timeout`)
   override config keys. `.env` can also set `ISSUEKIT_API_USER`,
   `ISSUEKIT_API_PASSWORD`, and `ISSUEKIT_API_TOKEN`.
3. `issuekit.local.toml`, for `worker` and `disabled_agents` only. Its
   `disabled_agents` list replaces the repository list rather than adding to
   it.
4. `[tool.issuekit]` in `pyproject.toml`, or `issuekit.toml`.
5. Machine config.
6. Built-in defaults.

Set `ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` in the process environment to skip the
local author-session STOP guard enforcement across checkouts. The same switch
makes claims send `allow_self_implement`, so the server author-implementer
guard is relaxed too;
the two guards cannot be relaxed separately. Unset or truthy values keep the
default enforcement behavior.

Repository config may name an API URL only when its origin matches the API URL
from machine config or appears in machine config's `trusted_api_origins` list.
Otherwise `load_config` fails and names the origins and sources. An API URL
from process environment or an untracked `.env` is accepted directly. The
resolved trust source is shown by `issuekit info` and MCP `health`.

Issuekit refuses to send credentials or bearer tokens to a non-loopback
`http://` URL. Loopback HTTP remains available for local development. To opt in
to a trusted plain-HTTP endpoint, set `ISSUEKIT_ALLOW_INSECURE=1` in the
process environment or set `allow_insecure_api_url = true` in machine config;
both opt-outs print a one-time stderr warning. `.env` and repository config
cannot enable this setting. Workers on a second machine using an HTTP API over
the LAN must set one of those two machine-controlled opt-outs there.

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
With `auto`, issuekit chooses the first configured assignee that differs from
the issue implementer. In API mode, `require_distinct_reviewer` is always true
for local decisions, so same-name review is rejected.

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

Repository config can tune how configured agents behave, but it cannot choose
the executable or its launch and permission arguments. Use the repository table
for model and reasoning defaults, speed selection, prompt text, role model and
reasoning overlays, and submit-time policy. Built-in agents can be patched by
name. A Python repository can use:

```toml
[tool.issuekit.agents.codex]
model = "gpt-6-sol"
reasoning_effort = "medium"
speed = true

[tool.issuekit.agents.codex.model_prompts]
"gpt-6-sol" = "Follow the gpt-6-sol project guidance."
```

For standalone `issuekit.toml`, use `[agents.codex]` and
`[agents.codex.model_prompts]` without the `tool.issuekit` prefix. Repository
config can also tune `mojibake_gate` and `diff_shape_warn_deletions`; see
[Encoding checks](#encoding-checks).

Set launch settings in machine config, which applies to every checkout on that
machine:

| Machine-only keys | Meaning |
|-----|---------|
| `binary`, `adapter`, `known_paths` | Executable and adapter selection. `binary` and each fallback entry must be a bare command or an absolute path. |
| `headless_argv`, `approval_flag`, `approval_value`, `roles.<role>.approval_argv` | Launch and permission arguments. |
| `output_format_flag`, `output_format`, `model_flag`, `effort_argv`, `speed_argv` | CLI argument templates and output format. |
| `resumable`, `session_flag`, `resume_flag` | Session support; see below. |
| `runtime`, `app_server_argv`, `lease_ttl_seconds` | Runtime selection; see the App Server paragraphs below. |

Use a machine config table such as `[agents.codex]` for those settings. A
custom agent must also be defined there; for example, `[agents.gemini]` starts
with `binary = "gemini"` and no other launch flags. Repository config may tune
that agent only after the machine config defines it.

The repository-supported agent keys are `model`, `reasoning_effort`, `speed`,
`roles`, `prompt_suffix`, `model_prompts`, `mojibake_gate`, and
`diff_shape_warn_deletions`. For example, per-role model and effort overlays
can be committed:

```toml
[tool.issuekit.agents.claude.roles.reviewer]
model = "claude-opus-5-5"
reasoning_effort = "high"
```

Role overlays accept `implementer`, `reviewer`, `router`, `triage`, and
`negotiation`. Repository role overlays support only `model` and
`reasoning_effort`. In machine config, a role's `approval_argv` replaces the
built-in launch policy and the agent-level `approval_flag` and
`approval_value`; it is a complete argument list, not an additive list. For
example, machine config can give Claude reviewers permission to run a project's
tests:

```toml
[agents.claude.roles.reviewer]
approval_argv = [
  "--permission-mode", "dontAsk",
  "--allowedTools",
  "Read,Grep,Glob,Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git ls-files:*),Bash(uv run pytest:*)",
  "--strict-mcp-config",
]
```

Use a narrowly scoped command pattern for the project's test command. The
`--allowedTools` value is one comma-separated argument. Keep
`--strict-mcp-config` when replacing a built-in Claude policy so configured
MCP servers remain disabled.

For the runtime boundary and how to add a config-only or custom agent adapter,
see [`issuekit/agentrun/README.md`](../../issuekit/agentrun/README.md).

Session flags come in a pair. `session_flag` starts a new agent session under a
caller-chosen id, and `resume_flag` continues a session that issuekit already
started; both require `resumable = true`. Negotiation uses the pair to keep one
session per side across the rounds of a run, so an agent configured with only
`session_flag` gets a fresh session per round as before. The built-in Claude
config sets `session_flag = "--session-id"` and `resume_flag = "--resume"`.

### Strict permission modes

For the default exec runtime, built-in Codex policies sandbox the read-only
roles and disable configured MCP servers with `-c mcp_servers={}`. Triage,
routing, and negotiation use `--sandbox read-only`; review uses
`--sandbox workspace-write`, which lets it run tests while keeping `.git`
read-only and network access off by default.
Every read-only evaluation also removes `ISSUEKIT_API_TOKEN`,
`ISSUEKIT_API_USER`, and `ISSUEKIT_API_PASSWORD` from the agent environment.
Its before-and-after repository check covers `.git/config`, files under Git's
hooks and info directories, and local credential or agent configuration files
such as `.env`, `issuekit.local.toml`, `.mcp.json`, and Claude settings.
On Linux, these defaults require Codex's bubblewrap sandbox to create
unprivileged user namespaces. issuekit probes the configured sandbox before
launch and stops with the probe error if the host blocks it. On Ubuntu with
AppArmor user-namespace restrictions, allow unprivileged user namespaces for
/usr/bin/bwrap, for example with an AppArmor profile containing `userns,` or
by setting `kernel.apparmor_restrict_unprivileged_userns=0`, then rerun. To
opt out for a role, set its `approval_argv` explicitly in machine config.
Implementer runs keep the existing unsandboxed defaults. For other `exec`
runtimes, set `approval_flag = "--sandbox"` and
`approval_value = "workspace-write"` in machine config to restrict filesystem
access. Enable network access by setting the machine config `headless_argv` to
`["exec", "-c", "sandbox_workspace_write.network_access=true"]`.
Codex CLI 0.147.0 added `--approve-for-me`, which uses `workspace-write` and
automatically reviews sandbox escalations. The old `codex exec --full-auto` flag
was removed in Codex CLI 0.147.0; use `--sandbox workspace-write` instead.
These overrides apply only to the default exec runtime; the App Server runtime
below ignores them.

Built-in Claude read-only roles use `--permission-mode dontAsk`, an allowlist
for `Read`, `Grep`, `Glob`, and read-only Git commands, and
`--strict-mcp-config` without `--mcp-config`. This prevents loading the user's
configured MCP servers. Implementer runs keep the existing
`bypassPermissions` default. Kimi has no built-in read-only launch policy, so
resolving it for a read-only role fails before launch unless
machine config `[agents.kimi.roles.<role>] approval_argv` is configured.

Claude reviewer Bash commands are limited to the read-only Git allowlist by
default. To run tests, add only the required project commands to a complete
role-level `approval_argv` list as shown above. The Claude Code Bash sandbox can
run tests while constraining filesystem and network access, but it depends on
platform sandbox support and dependencies such as `bubblewrap` and `socat` on
Linux. If the sandbox cannot be used, keep the restricted allowlist and add
only the test commands the reviewer needs.

Machine config controls `headless_argv`; its entries go before the prompt. Do
not put a variadic Claude option
such as `--allowedTools <tools...>` last, because it can consume the prompt;
configure allow rules in `.claude/settings.json` instead. To tell Claude that
no one can answer permission prompts during an unattended run, include
`headless_argv = ["-p", "--permission-prompts", "none"]` (`--permission-prompts`
requires Claude Code 2.1.259 or later). In a `-p` run without a permission host,
the flag also tells Claude not to retry denied requests.

Codex implementation runs use `codex exec` by default. API-backed projects can
opt into issue-owned App Server attempts for Codex implement and serve runs by
setting this in machine config:

```toml
[agents.codex]
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
existing submit-for-review workflow. A transient `request_failed` heartbeat
retries until half the TTL has elapsed since the last successful heartbeat. A
provider without the routes returns a clear unsupported-runtime error;
issuekit does not silently fall back because the mode is explicit. App Server
is Codex-only and
implementer-only in this version. Both `implement` and `serve` honor this
runtime. Its threads start and resume with approval policy `never` and the
`danger-full-access` sandbox, and it reads only `binary`,
`known_paths`, `lease_ttl_seconds`, `app_server_argv`, `model`,
`reasoning_effort`, `prompt_suffix`, and `model_prompts` from the agent
config: `approval_flag`, `approval_value`, `approval_argv`,
`headless_argv`, `speed`, and `speed_argv` have no effect in this mode. The
`issuekit implement --follow` heartbeat
applies only to the default exec runtime; it polls `git status` read-only without
an index lock, so it is safe for issues that rewrite the checkout.

On implementer runs, both runtimes send the plan pointer plus the agent's
`prompt_suffix`; every run also receives any matching `model_prompts` entry.
The exec prompt names `ISSUEKIT_IMPLEMENTER_REPORT_FILE` as the report
destination; the App Server prompt replaces it with the concrete path because
its session does not receive the exec environment. App Server attempts also
record token usage. Codex reports it through `thread/tokenUsage/updated`
notifications, which issuekit uploads as `turn_progress` events carrying a
`usage` payload with `last` and `total` breakdowns. The cumulative thread total
is repeated in the final `runtime_stopped` event, in the run's
`<run_id>.out.log` JSON, and as `usage_*` entries on the run result, so App
Server and exec runs can be compared without reading the raw agent log.

The built-in Claude config bypasses permissions so headless implementer runs
can execute shell commands unattended. Stricter projects can set
`[agents.claude] approval_value = "acceptEdits"` in machine config; see
[Strict permission modes](#strict-permission-modes) for its `-p` limitations
and command allow rules.

#### Claude `-p` assumptions

Issuekit relies on non-bare Claude `-p` behavior for instruction-file discovery,
`.mcp.json` servers, and OAuth sign-in. See the [Claude Code headless
documentation](https://code.claude.com/docs/en/headless). If a future Claude
Code release makes `--bare` the default for `-p`, add its documented opt-out
flag through machine config `[agents.claude] headless_argv`.

That config also sets `output_format = "json"`, so Claude returns a result
envelope instead of bare text. An agent configured with `output_format = "json"`
has its envelope unwrapped by the adapter: `stdout` becomes the agent's own
reply, exactly as under `"text"`, and the run result gains `session_id`,
`cost_usd`, `usage_*`, `is_error`, `terminal_reason`, `num_turns`,
`permission_denials`, `permission_denied_tools`, `api_error_status`,
`fast_mode_state`, and `fast_mode_disabled_reason` entries when the envelope
carries them, plus `failure_reason` (the envelope's result text) when
`is_error` is true. The unwrapped run log keeps the full
envelope, and an agent that dies before emitting one keeps its raw stdout, so
crash diagnostics are unchanged. Set `output_format = "text"` to opt out; the
recorded metrics are then unavailable.

The built-in Codex adapter invokes `codex exec --json` and parses its JSONL
events. `stdout` contains the last completed agent message, while the run result
includes `session_id`, `usage_*`, and error fields when the events provide them.
The run status record keeps the session id, token counts, final message, and
error flag. Raw JSONL remains in the `.out.log` file, and output without
parseable events is preserved as raw stdout.

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
model = "claude-opus-5-5"
```

Role overlays accept only `model` and `reasoning_effort`, and take precedence
over the agent default but not explicit per-run values. This lets one agent
name use different settings across implement-mode and `serve --review`
processes, or for implementer and triage work within one `serve --triage` loop.
An agent must define `effort_argv` to support `reasoning_effort`; the
built-in Codex adapter uses `("-c", "model_reasoning_effort={value}")` and the
built-in Claude adapter uses `("--effort", "{value}")`. The `speed` setting is
a boolean switch; `true` emits the agent's `speed_argv` entries verbatim, while
`false` or an absent setting emits nothing. The built-in templates carry each
CLI's wire value: Codex uses `("-c", "service_tier=fast")`, and Claude uses
`("--settings", '{"fastMode": true}')`. For Codex, Fast mode requires
`features.fast_mode`, which is enabled by default, and ChatGPT sign-in. Fast
mode is available only for models that support it. See the current
[Codex speed configuration](https://developers.openai.com/codex/agent-configuration/speed)
for details. Current issuekit rejects the old string form:
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
The guard never switches branches. For human recovery, the CLI `claim`,
`implement`, `serve`, and `submit-review` commands accept `--allow-any-branch`;
the MCP `claim_next_task` and `submit_for_review` tools accept
`allow_any_branch`. Omit `work_branch` or set it to an empty string to disable
the guard, which is the default. The config shape is intended to grow later to
an allowed branch list or glob such as
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

Issuekit's claim-sync fetch and fast-forward merge use an empty hooks directory,
so they do not run repository-configured Git hooks.

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

Run `issuekit add` from a git-managed checkout. The
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
hold_auto_adopted = true
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

`hold_auto_adopted` defaults to `true`. Mechanical adoption, triage-author
adoption, and proposal-check approval leave the issue at `planned` until a
human releases it with `issuekit plan <id> --stage todo`. Set it to `false` to
restore automatic claiming after adoption. Until issuekit#403 binds proposal
origins to authenticated senders, `trusted_origins` is not authentication and
disabling this hold allows forged origins to reach unattended implementation.

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
