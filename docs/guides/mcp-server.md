# MCP server

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

## Developer commands

Issuekit developers working from a checkout should use the repeatable developer
commands instead:

```powershell
uv run issuekit dev-tool install-editable
uv run issuekit dev-tool reload-mcp
uv run issuekit dev-tool reinstall
```

`install-editable` and `reinstall` are Windows-only; on other platforms, or
when `--repo` is not an issuekit checkout, they print an error diagnostic,
report `ok: false`, exit 1, and change nothing. Both stop running
`issuekit-mcp.exe` processes first unless you pass `--no-stop`. `reload-mcp`
works on Windows and POSIX.

`install-editable` reflects source edits the next time a global `issuekit` or
`issuekit-mcp` process starts. `reload-mcp` stops only matching MCP server
processes (`issuekit-mcp.exe` on Windows; on POSIX, processes whose command
line runs an `issuekit-mcp` script) and reports their PIDs and executable paths
when available. It cannot respawn or reconnect an already-open codex or Claude
Code stdio MCP transport; the MCP client owns that connection. If MCP tools
still return `Transport closed` after `reload-mcp`, reload or restart the MCP
client session, reload the thread/window if supported, or start a fresh session
so the client can spawn a new `issuekit-mcp` transport. `reinstall` is the
recovery path when editable metadata gets stale or a global tool environment is
partially broken. All generated uv install commands use an absolute checkout
path, never a bare `.`.

## Per-repo scaffolding

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

This still scaffolds the repo, but prints one JSON object with `ok`,
`client_transport_check`, `scaffold` (`written`, `skipped`, and `guidance`),
and `diagnostics` fields instead of the human checklist. `ok: false` means at
least one diagnostic still needs action, often an optional global install or
configuration step; the command still exits 0 when repo scaffolding succeeds.

Orchestrators that only need a preflight should use the read-only check:

```powershell
issuekit setup check --json
```

The check does not write files or run subprocesses. Its JSON object reports
`ok`, `state`, `needs_setup`, `would_write`, `would_update`,
`client_transport_check`, `diagnostics`, and `actions` so automation can decide
whether to run the applying command. `state` summarizes the actions as
`current`, `missing`, `stale`, or `blocked`. `client_transport_check.status` is
`unsupported_from_cli` because a standalone CLI can verify static readiness but
cannot prove that an already-open codex or Claude Code stdio transport is live.
`issuekit setup --check` is an alias for `issuekit setup check`.
`issuekit setup` keeps its applying behavior, and `issuekit setup apply --json`
is an explicit alias for that path.

Because it runs `init`, `issuekit setup` writes the base init files:
`.gitattributes`, `.editorconfig`, the issues directory `README.md`
(`docs/issues/README.md` by default), `.pre-commit-config.yaml` with the
`check-encoding` and `author-guard` hooks, and the `issuekit.local.toml` and
`.agent-runs/` entries in `.gitignore`. Existing templated files are skipped
unless you pass `--force`. Force refreshes only the issuekit MCP entries and
issuekit-owned template files (`.gitattributes`, `.editorconfig`, and the issues
README); it preserves other MCP servers and settings. An existing
`.pre-commit-config.yaml` is never overwritten, and missing hooks get printed
guidance instead of an edit.

The MCP part of the scaffold writes `.mcp.json`, appends `.codex/config.toml`
when needed, and adds thin handoff references to `AGENTS.md` and `CLAUDE.md`.
The generated MCP entries run the global `issuekit-mcp` binary; they do not use
`uv run`, so they work outside the issuekit checkout. Launch codex or Claude
Code from the target repo root so the server resolves repo configuration.

## Health and troubleshooting

When the MCP transport is live, the MCP `health` tool reports configuration
status without calling the tracker or mutating issue lifecycle state. Its
object has `ok`, `version`, `cwd`, `project`, `api_url_configured`,
`api_url_source`, `api_url_origin`, `repo_config_source`, `machine_config_path`,
`machine_config_status`, `env_present`, `token_cached`, `token_expires_at`,
`worker_present`, `worker`, `author_guard_active`, `author_guard`, and `errors`.
`api_url_origin` includes only the URL scheme, host, and port.
`machine_config_status` is `missing`, `readable`, or
`unreadable: <ExceptionClass>`. `env_present` maps selected `ISSUEKIT_*` names
and `XDG_CONFIG_HOME` to booleans; it never reports their values. `ok` is false
and `errors` lists the cause when `issuekit.local.toml` or the issuekit config
cannot be loaded. `token_cached` and `token_expires_at` describe the cached API
token for the configured API URL.

### Environment seen by the MCP server

MCP clients can start `issuekit-mcp` with a filtered environment. Codex forwards
only its default allowlist and variables named in the server's `env_vars` list.
For a repository server, add the settings issuekit uses to
`.codex/config.toml`:

```toml
[mcp_servers.issuekit]
command = "issuekit-mcp"
args = []
env_vars = ["ISSUEKIT_API_URL", "ISSUEKIT_PROJECT", "ISSUEKIT_CONFIG", "ISSUEKIT_TOKEN_CACHE", "ISSUEKIT_ALLOW_INSECURE", "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF", "XDG_CONFIG_HOME"]
```

Alternatively, set `api_url` in the machine config at
`~/.config/issuekit/config.toml` (or the path selected by `ISSUEKIT_CONFIG`).
The MCP server re-reads TOML config on each tool call. Its process environment
and values loaded from `.env` are fixed at server start, so restart the MCP
server and reload the client session after changing them.

Compare `issuekit info --json` fields `apiUrlSource` and `apiUrlOrigin` with
MCP `health` fields `api_url_source` and `api_url_origin`. A client config `env`
block (`.mcp.json` `env` or Codex `env`) overrides a value inherited from the
shell, while `api_url_source` still reports `env` for either case. Tokens are
cached per exact `api_url` string. If the client uses a second spelling of the
same server, run `issuekit login` with `ISSUEKIT_API_URL` set to that spelling
to cache a token for it. Compare `apiUrlOrigin` and `api_url_origin` when both
processes report `env`; the origin omits userinfo, path, query, and fragment.

`cwd` is the resolved config root that the MCP tools load configuration
from. The server uses its own working directory when that directory has
`issuekit.toml` or a `[tool.issuekit]` table in `pyproject.toml`. Otherwise it
tries the enclosing git root, which qualifies when it has such config or the
machine config sets `api_url`, and then each workspace root the MCP client
reports, with the same checks. If none qualifies, it falls back to the server
working directory. A `cwd` pointing somewhere unexpected usually means the
client launched the server outside the repo and did not report a matching
workspace root.

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

## MCP tools

The server registers these tools. The listed CLI command performs the same
operation, so it is the fallback when the MCP transport is down.

| Tool | Purpose | CLI equivalent |
|------|---------|----------------|
| `health` | Read-only server and configuration status. | none; `info --json` is closest |
| `get_protocol` | Read the handoff protocol for an agent or role. | `protocol` |
| `claim_next_task` | Claim the next eligible issue for an implementer. | `claim` |
| `submit_for_review` | Submit an implemented issue for review. | `submit-review` |
| `next_review` | Read the next issue waiting for a reviewer. | `next-review` |
| `request_changes` | Return a review issue to its implementer with notes. | `request-changes` |
| `approve` | Approve a review issue and complete it. | `approve` |
| `get_issue` | Read one active or completed issue. | `show` |
| `update_issue` | Edit title, body, appended text, priority, or dependencies. | `edit` |
| `list_queue` | List active issues, filtered by assignee and stage. | `queue` |
| `list_workers` | List registered workers and their roles. | `workers` |
| `remove_worker` | Remove a registered worker. | `workers remove` |
| `remove_repo` | Remove a repo catalog entry. | `repos remove` |
| `list_orphans` | List implementing claims whose worker is gone or silent. | `orphans` |
| `reclaim_issue` | Return an orphaned claim to the implement pool. | `reclaim` |
| `readdress_issue` | Return a directed issue to the repo pool. | `readdress` |
| `dispatch_issue` | Direct an issue to a registered worker. | `dispatch` |
| `list_project_profiles` | List stored project capability profiles. | `profile --all` |
| `propose` | Send a proposal to another project's inbox. | `propose` |
| `list_incoming` | List pending incoming proposals. | `incoming` |
| `list_outgoing` | List proposals this project sent to a target. | `outgoing --to` |
| `list_negotiation_threads` | Inspect negotiation threads without launching agents. | `threads` |
| `adopt_proposal` | Adopt an incoming proposal as an active issue. | `adopt` |
| `discard_proposal` | Discard an incoming or sent pending proposal. | `discard` |
| `create_proposal_check` | Ask a target worker to evaluate a proposal. | `proposal-check-request` |
| `list_proposal_checks` | List proposal checks addressed to this checkout. | `proposal-checks --list` |

## MCP boundary

The MCP surface is for tracker reads and state changes performed by the calling
agent. Commands that launch other agents remain CLI orchestration: `implement`,
`review`, `serve`, `triage`, `request`, and `negotiate`. In particular,
negotiation can hold a stdio transport open for several agent turns, so running
it from an MCP agent session makes a fragile transport failure more likely.

Running proposal checks is CLI-only: use `issuekit serve --proposal-checks
--proposal-check-limit <n>` or `issuekit proposal-checks --agent <a> --once`.
Authors can request a check through the MCP `create_proposal_check` tool.
The removed `run_proposal_checks` tool was added under the mirror-the-CLI
instruction in issuekit#158, which issuekit#295 has replaced. That former
exception is not precedent for exposing more agent-launching orchestration
through MCP. Read-only negotiation thread inspection is available through
`list_negotiation_threads`.
