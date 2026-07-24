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
```

When both files exist, `[tool.issuekit]` in `pyproject.toml` takes precedence.
If `pyproject.toml` exists without `[tool.issuekit]`, issuekit falls back to
`issuekit.toml`.

## Machine config

Machine-wide defaults can be stored in `~/.config/issuekit/config.toml` on both
platforms (on Windows, `%USERPROFILE%\.config\issuekit\config.toml`).
`XDG_CONFIG_HOME` is honored on both platforms. Set `ISSUEKIT_CONFIG` to use an
explicit file, or set it to an empty string to disable machine config loading.
Existing Windows users with a config under `%APPDATA%` should move it to
`%USERPROFILE%\.config\issuekit\` or set `ISSUEKIT_CONFIG`. Machine config has
lower precedence than repository config and cannot define `worker`; checkout
registration belongs in `issuekit.local.toml`. Agent tables merge by key between
machine and repository layers, while other values are replaced by the
higher-precedence layer. Repository identity settings such as `project`,
`work_branch`, `issues_dir`, and `profile_*` normally belong in repository
config.

Set `default_implementer` in machine config when one agent is the usual worker
on that machine. Commands and MCP tools that omit an implementer use it before
falling back to a single enabled assignee; repository config can override it.

Use `[agent_roles]` in machine config to select the protocol each agent sees.
For example, when Claude is the implementer on that machine:

```toml
[agent_roles]
claude = "implementer"
```

Each agent has exactly one default role in this table; it cannot be configured
with two. When one agent serves more than one role, request the non-default
protocol explicitly with `issuekit protocol --role <role>` or
`get_protocol(role=...)`. The explicit role always wins over the agent default,
so prefer it whenever the role is known, including when `[agent_roles]` is
unset. Run `issuekit info` to inspect the effective mapping under `Agent roles`,
including built-in defaults, before relying on `--agent`.

`[agent_roles]` selects protocol text only. Role-scoped model overlays such as
`[agents.<name>.roles.<role>]` are resolved by the launch site instead:
`issuekit review` passes `reviewer` and `issuekit implement` passes
`implementer`, while `issuekit request` passes `router`. Model and effort
selection is therefore unaffected by `[agent_roles]`.

## Environment and precedence

At startup, issuekit also reads a repo-local `.env` file from the current repo
root and loads values such as `ISSUEKIT_API_URL`, `ISSUEKIT_API_USER`,
`ISSUEKIT_API_PASSWORD`, `ISSUEKIT_API_TOKEN`, `ISSUEKIT_TOKEN_CACHE`, and
`ISSUEKIT_PROJECT`. Existing process environment variables are not overwritten.
Overall precedence is per-run CLI flags, process environment, `.env`, then
`[tool.issuekit]` or `issuekit.toml`, machine config, and built-in defaults.
Set `ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` to skip only the local author-session
STOP guard enforcement across checkouts; unset or truthy values keep the default
enforcement behavior.

When `ISSUEKIT_API_URL` uses plain `http://` for a non-loopback host, issuekit
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

## Agent overlays

Built-in agent configs can be patched by name. A table such as
`[tool.issuekit.agents.codex]` overlays only the keys it specifies and leaves
other built-in agents unchanged. For standalone `issuekit.toml`, use the same
table without the `tool.issuekit` prefix:

```toml
[tool.issuekit.agents.codex]
approval_flag = "--full-auto"
model = "gpt-5.6"

[tool.issuekit.agents.codex.model_prompts]
"gpt-5.6" = "Follow the gpt-5.6 project guidance."
```

For the runtime boundary and how to add a config-only or custom agent adapter,
see [`issuekit/agentrun/README.md`](../../issuekit/agentrun/README.md).

By default, issuekit runs Codex without a sandbox and relies on the repository
worktree plus the review gate. Projects that require the strict sandbox can use
the override above, or set `approval_flag = "--sandbox"` and
`approval_value = "workspace-write"`.

The built-in Claude config bypasses permissions so headless implementer runs
can execute shell commands unattended. Stricter projects can restore the old
behavior with `[agents.claude] approval_value = "acceptEdits"` in repo or
machine config.

Agent-launching commands accept pass-through `--model <model-id>` and
`--reasoning-effort <value>` overrides, including `implement`, `review`,
`negotiate`, `request`, `serve`, `triage`, and `proposal-checks`. Issuekit does not
restrict model ids; the selected agent CLI validates them. The `model` and
`reasoning_effort` agent overlays set defaults, while `model_prompts` adds
prompt text for keys matching the exact resolved model id. Explicit per-run
values take precedence over configured defaults. A serve override applies to
every agent launched by that loop, so mixed-agent serve setups should configure
`model` and `reasoning_effort` in each agent's overlay instead. An agent can
also set model and reasoning-effort defaults for the `implementer`, `reviewer`,
`router`, or `triage` role:

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
built-in Claude adapter uses `("--effort", "{value}")`.

By default, agent-launched implementation and review transitions report the
effective model and reasoning effort to mine-py. Set `send_agent_runtime = false`
when using a mine-py deployment older than mine-py#579: that server rejects the
additional fields with HTTP 422, so the transition fails rather than omitting
the runtime data.

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
to disable this guard. `claim_sync_interval_sec` defaults to `60` and limits
how often a successful fetch runs for the same checkout and branch.

The guard does not run without `work_branch`. It blocks a dirty checkout, a
failed `git status`, or a failed fetch or fast-forward so the operator can fix
the checkout and retry. After inspecting a known-safe situation, such as
leftover work from a timed-out implement run, pass `--no-sync` to `claim`,
`implement`, or `serve` to deliberately skip this guard for that command; the
MCP `claim_next_task` tool accepts `no_sync` for the same purpose.

## Encoding checks

The agent submit mojibake gate checks half-width katakana by default, matching
`issuekit check-encoding`. Set `gate_halfwidth_kana = false` only when touched
generated files legitimately contain half-width katakana; other encoding-artifact
checks remain enabled. The gate also honors `check_encoding_exclude` for
unconfirmed hits: use a narrow repo-relative path glob for a tree with
known-legitimate Japanese text. Confirmed corruption still blocks submission in
every path.

For likely mojibake, `check-encoding` has three outcomes: confirmed candidates
are reported, unconfirmed candidates are suppressed but available through
`--show-unconfirmed-mojibake` and `unconfirmed_mojibake_hits`, and other text is
not a candidate. Unconfirmed means inconclusive, not proven legitimate. CI can
use `--fail-on-unconfirmed` to report and fail on unconfirmed candidates, but it
also fails on legitimate Japanese that is indistinguishable from lossy
corruption. Use it only with `check_encoding_exclude` entries for trees that
legitimately contain such text, or when the project has none.

Set `check_encoding_exclude` to a list of POSIX-style, repo-relative glob
patterns for generated paths that `issuekit check-encoding` should skip. The
agent submit mojibake gate also honors them for unconfirmed hits; confirmed
mojibake remains blocked by that gate. The exclusions apply to BOM, mojibake,
stray carriage-return, and CRLF checks. Use repeatable `--exclude PATTERN` flags
for one-off exclusions.

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
