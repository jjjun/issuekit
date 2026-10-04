# Agent runtime boundary

`issuekit/agentrun/` is the small, reusable runtime for invoking a headless
coding-agent CLI. It deliberately imports no issuekit application layer; its
dependencies on the rest of the package are the leaf helpers `issuekit.coerce`,
`issuekit.gitutil`, and `issuekit.file_permissions`. This keeps process
execution independent of issue tracker state and makes the boundary clear when
adding agent-related code.

## Layers

`issuekit/agentrun/` locates a headless agent CLI, builds its argv, spawns and
supervises the process, enforces timeouts, kills the process group, and writes
run logs and status JSON. `app_server.py` holds the stdio JSON-RPC transport
and command journal for `codex app-server`.

If an agent run is interrupted while waiting for the process, the runtime kills
its process group, writes a terminal `failed` status with exit code `130`, then
re-raises the interruption. One-shot agent commands translate SIGTERM into this
same interruption path.

`issuekit/agents/` contains the issuekit workflows that use an agent:
`run_claimed`, `app_server_runtime`, `review`, `proposal_check`,
`proposal_eval`, `triage_author`, `triage_state`, and `router`, plus the shared
`readonly` helper. These workflows own tracker state and import `workflow`,
`store`, and `proposals` as needed.

`issuekit/agents/registry.py` is the seam between the layers. It is the place
that reads `IssuekitConfig` to produce an adapter for the runtime. The one
exception is `run_claimed`, which reads `config.agents` directly to choose the
App Server runner when an agent sets `runtime = "codex_app_server"`.

`issuekit/agentrun/` may import only its own runtime modules and the leaf modules
`issuekit.coerce`, `issuekit.gitutil`, and `issuekit.file_permissions` from the
rest of the package. Leaf modules import only the standard library and other
leaf modules. Application modules may also use these leaves.

When adding code, put CLI launch, process supervision, and run-artifact logic
in `agentrun`; put issue lifecycle, proposal, and workflow decisions in
`agents`.

## Configuration split

`AgentRunConfig` in `issuekit/agentrun/config.py` holds the effective settings
used to launch an agent. Machine config owns executable selection and launch
or permission arguments; repository config can tune `model`,
`reasoning_effort`, `speed`, prompt text, and role model or effort overlays.
Repository config can also set `mojibake_gate` and
`diff_shape_warn_deletions`, which `AgentPolicy` in
`issuekit/config/settings.py` applies as issuekit policy. Both layers use an
`[agents.<name>]` table, but only machine config may define a new agent or set
launch keys. Python repositories use `[tool.issuekit.agents.<name>]` for the
repository-supported tuning keys.

## Add an agent

For a config-only agent, define it in machine config with an
`[agents.<name>]` table containing the CLI `binary`, `headless_argv`, approval
and output flags, `model_flag`, `effort_argv`, and `speed_argv`. Repository
config may then tune its `model`, `reasoning_effort`, `speed`, and prompt text.
The agent uses `ConfigAgentAdapter`; no code change is required. `speed` is a
boolean switch, and machine-config `speed_argv` contains the literal per-CLI
arguments emitted when it is true. Executables must be bare command names or
absolute paths so issuekit never resolves one from the current checkout.

Create a custom adapter only when its CLI needs behavior declarative
configuration cannot express. `issuekit/agentrun/adapters/kimi.py` overrides
`parse_output` to recover a resumable session id from stderr, while
`issuekit/agentrun/adapters/codex.py` parses Codex exec JSONL events. Subclass
`ConfigAgentAdapter`, register the class in
`issuekit/agentrun/adapters/registry.py`, and set `adapter = "<marker>"` in the
agent configuration.

See [`docs/guides/configuration.md`](../../docs/guides/configuration.md) for
the user-facing TOML reference.

### Claude `-p` assumptions

Issuekit relies on non-bare Claude `-p` behavior for instruction-file
discovery, `.mcp.json` servers, and OAuth sign-in. See the [Claude Code headless
documentation](https://code.claude.com/docs/en/headless). If a future Claude
Code release makes `--bare` the default for `-p`, add its documented opt-out
flag through machine config `[agents.claude] headless_argv`.

## Run artifacts

Each exec runtime invocation reserves a run id with a `<run_id>.lock` file and
produces `<run_id>.out.log`, `<run_id>.agent.log`, and
`<run_id>.status.json`, plus `<run_id>.report.md` for implementer runs. App
Server attempts use run ids of the form `app-server-<issue>-<hex>` and write
`.out.log`, `.agent.log`, `.report.md`, and `.commands.jsonl` files without a
lock or status file. The `.agent-runs/` directory is gitignored. On POSIX the
directory and its files are owner-only; on Windows the permission helpers leave
the inherited ACLs unchanged.

Other issuekit components also use this directory: serve stores `serve.lock`
and `serve.log`, prompts are written there, and triage-author keeps its state
there. Treat it as shared local run storage rather than a directory owned only
by the runtime.

## Codex App Server recovery

The App Server runner currently derives its recovery flag from whether the run
has review feedback. That flag adds a worktree-inspection preamble and permits
resuming a prior native thread only when the machine, repository, checkout, and
session state match. An orphaned attempt without review feedback starts a new
native thread. `thread/resume` sends the thread id, checkout path, and the same
`never` approval policy and `danger-full-access` sandbox used for new threads.

The runner checks whether the App Server process is still alive while waiting
for provider commands, and reports its exit status and stderr tail if it exits
during a turn. Transient `request_failed` heartbeat errors retry until half the
lease TTL has elapsed since the last successful heartbeat. `turn/start` and
`turn/steer` input is bounded to Codex's 1 MiB limit, with a warning when text
has to be truncated.
