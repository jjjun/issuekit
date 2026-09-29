# Agent runtime boundary

`issuekit/agentrun/` is the small, reusable runtime for invoking a headless
coding-agent CLI. It deliberately imports no issuekit application layer; its
only dependency on the rest of the package is the leaf helper
`issuekit.file_permissions`. This keeps process execution independent of issue
tracker state and makes the boundary clear when adding agent-related code.

## Layers

`issuekit/agentrun/` locates a headless agent CLI, builds its argv, spawns and
supervises the process, enforces timeouts, kills the process group, and writes
run logs and status JSON. `app_server.py` holds the stdio JSON-RPC transport
and command journal for `codex app-server`.

`issuekit/agents/` contains the issuekit workflows that use an agent:
`run_claimed`, `app_server_runtime`, `review`, `proposal_check`,
`proposal_eval`, `triage_author`, `triage_state`, and `router`, plus the shared
`readonly` helper. These workflows own tracker state and import `workflow`,
`store`, and `proposals` as needed.

`issuekit/agents/registry.py` is the seam between the layers. It is the place
that reads `IssuekitConfig` to produce an adapter for the runtime. The one
exception is `run_claimed`, which reads `config.agents` directly to choose the
App Server runner when an agent sets `runtime = "codex_app_server"`.

Nothing under `issuekit/agentrun/` may import `issuekit.config`,
`issuekit.workflow`, `issuekit.store`, or `issuekit.proposals`. The runtime
has its own `_coerce.py` and `git.py` because it needs a few helper functions
without taking dependencies on `issuekit.core` and `issuekit.gitutil`.

When adding code, put CLI launch, process supervision, and run-artifact logic
in `agentrun`; put issue lifecycle, proposal, and workflow decisions in
`agents`.

## Configuration split

`AgentRunConfig` in `issuekit/agentrun/config.py` holds launch settings only.
`AgentPolicy` in `issuekit/config/settings.py` holds issuekit policy, including
`mojibake_gate` and `diff_shape_warn_deletions`. Both are configured through
the same `[agents.<name>]` TOML table (or `[tool.issuekit.agents.<name>]` in
`pyproject.toml`), so this internal split does not change the configuration
surface.

## Add an agent

For a config-only agent, add an `[agents.<name>]` table with the CLI `binary`,
`headless_argv`, approval and output flags, `model_flag`, `effort_argv`,
`speed`, and `speed_argv`. The agent then uses `ConfigAgentAdapter`; no code
change is required. `speed` is a boolean switch, and `speed_argv` contains the
literal per-CLI arguments emitted when it is true.

Create a custom adapter only when its CLI needs behavior declarative
configuration cannot express. For example,
`issuekit/agentrun/adapters/kimi.py` overrides `parse_output` to recover a
resumable session id from stderr. Subclass `ConfigAgentAdapter`, register the
class in `issuekit/agentrun/adapters/registry.py`, and set
`adapter = "<marker>"` in the agent configuration.

See [`docs/guides/configuration.md`](../../docs/guides/configuration.md) for
the user-facing TOML reference.

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
