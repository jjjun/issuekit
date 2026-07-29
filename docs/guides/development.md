# Development

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

See [Testing](testing.md) for the gates to run before publishing changes, and
[`issuekit/agentrun/README.md`](../../issuekit/agentrun/README.md) for the agent
runtime boundary.

Run the repository-owned Ruff configuration with:

```powershell
uv run ruff check
uv run ruff check --fix
```
