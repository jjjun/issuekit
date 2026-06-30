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
```

## MCP server

Install or upgrade the MCP server once as a global tool:

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

When upgrading a global tool, stop running issuekit MCP servers first. MCP
clients hold `issuekit-mcp.exe` while the session is running, so replacing the
tool underneath them can leave the install half-removed. Prefer:

```powershell
uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"
```

Use an absolute path or URL, not a bare `.`, to avoid cwd-dependent installs.
After any reinstall or upgrade, restart running codex and Claude Code sessions;
they keep a stdio connection to the old server process.

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
`ok`, `needs_setup`, `would_write`, `would_update`, `diagnostics`, and `actions`
so automation can decide whether to run the applying command. `issuekit setup`
keeps its applying behavior, and `issuekit setup apply --json` is an explicit
alias for that path.

The repo scaffold writes `.mcp.json`, appends `.codex/config.toml` when needed,
and adds thin handoff references to `AGENTS.md` and `CLAUDE.md`. The generated
MCP entries run the global `issuekit-mcp` binary; they do not use `uv run`, so
they work outside the issuekit checkout. Launch codex or Claude Code from the
target repo root so the server resolves repo configuration.

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
| `issuekit complete <id> --summary "..." --verification "..."` | Complete an issue through the API. |
| `issuekit approve <id> --verification "..." [--reviewer claude]` | Approve a review-stage issue and move it to completed. |
| `issuekit claim --assignee codex` | Claim the next active issue for an implementer. |
| `issuekit submit-review <id> --summary "..." [--assignee codex] [--reviewer claude]` | Submit implemented work to a reviewer. |
| `issuekit request-changes <id> --notes "..." [--assignee codex] [--reviewer claude]` | Return a reviewed issue to implementation. |
| `issuekit queue --assignee claude [--stage review]` | List active issues for an assignee. |
| `issuekit check-encoding [--json]` | Check tracked source files for leading BOM bytes and likely mojibake. |
| `issuekit protocol [--agent codex\|claude]` | Print the canonical handoff protocol. |
| `issuekit init [--with-mcp]` | Install tracker templates, encoding hooks, and optional MCP handoff scaffolding. |
| `issuekit setup [--force] [--json]` | Run per-repo MCP handoff scaffolding and setup diagnostics. |
| `issuekit setup check --json` | Check setup state without writing files. |
| `issuekit add-ref <name> --path <repo> [--scope local\|workspace]` | Register an optional local project alias. |
| `issuekit list-refs` | List effective local project aliases and their source. |
| `issuekit propose --to <project> --title "..."` | Send a proposal to a project API inbox. |
| `issuekit incoming [--json]` | List inbound API proposals. |
| `issuekit adopt <proposal-id>` | Adopt an incoming API proposal as a local issue. |
| `issuekit discard <proposal-id>` | Discard an incoming API proposal. |

Legacy file parsing is used only by `migrate-to-api`,
`migrate-proposals-to-api`, and the explicit filesystem issue-store escape
hatch.

## Cross-Project Proposals

Related projects exchange suggestions through API proposal inboxes. Proposals
are not workflow items until they are adopted, so the API-backed issue queue is
separate from proposal triage.

`--to` takes the target API project key. The old workspace ref registry is kept
only as an optional local alias map for operators who want to remember sibling
project names; proposal delivery no longer resolves a target repo path or writes
files. For a set of sibling repos, place one `issuekit.workspace.toml` above
them:

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
stages = ["todo", "implementing", "review", "changes_requested", "done"]
default_reviewer = "auto"
require_distinct_reviewer = true
```

Non-Python repositories can use a standalone `issuekit.toml` at the repo root
with the same keys at the top level:

```toml
api_url = "https://mine.example"
project = "issuekit"
assignees = ["codex", "claude"]
stages = ["todo", "implementing", "review", "changes_requested", "done"]
default_reviewer = "auto"
require_distinct_reviewer = true
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

At startup, issuekit also reads a repo-local `.env` file from the current repo
root and loads values such as `ISSUEKIT_API_URL`, `ISSUEKIT_API_USER`,
`ISSUEKIT_API_PASSWORD`, `ISSUEKIT_API_TOKEN`, `ISSUEKIT_TOKEN_CACHE`, and
`ISSUEKIT_PROJECT`. Existing process environment variables are not overwritten.
Overall precedence is process environment, then `.env`, then `[tool.issuekit]`
or `issuekit.toml`, then built-in defaults.

When both files exist, `[tool.issuekit]` in `pyproject.toml` takes precedence.
If `pyproject.toml` exists without `[tool.issuekit]`, issuekit falls back to
`issuekit.toml`.

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

## Development

This repo dogfoods issuekit. Implementation tasks and cross-project proposals
live in the configured API project.
