# Checking issuekit-mcp from real Claude Code and Codex clients

**Applies to:** reviewing MCP server changes against live clients, without
touching the user's configured `issuekit` MCP entry

Point each client at the checkout's own server binary,
`<checkout>/.venv/bin/issuekit-mcp` after `uv sync`, under a throwaway
server name such as `ikt`. Use a deterministic read-only error call such as
`get_protocol(role="bogus")`. That call should return `isError` with
`Error executing tool get_protocol: unknown role: bogus`. If it shows only
`Error executing tool get_protocol`, the client cannot see tool error
messages.

Claude Code: run `claude -p --strict-mcp-config --mcp-config <json>
--permission-mode dontAsk --allowedTools "mcp__ikt__health,..."` and pass
the prompt on stdin. `--allowedTools` is variadic and swallows a trailing
prompt argument, which fails with "Input must be provided either through
stdin or as a prompt argument".

Codex (verified with 0.158.0): use `codex exec -s read-only -c
'mcp_servers.ikt.command="<binary>"' -c 'mcp_servers.ikt.args=[]'`. With the
usual `approval_policy = "never"`, every MCP call fails with "MCP tool call
requires approval, but approval policy is never" until you add
`-c 'mcp_servers.ikt.default_tools_approval_mode="approve"'`. Valid values
are `auto`, `prompt`, `writes`, and `approve`.

Codex also filters the environment it passes to MCP servers. If the API URL
comes only from `ISSUEKIT_API_URL` and related variables, API tools fail with
the missing-api_url WorkflowError until you forward them, for example with
`-c 'mcp_servers.ikt.env_vars=["ISSUEKIT_API_URL","ISSUEKIT_API_USER"]'`.
Servers on mcp 1.x and 2.x behave the same way here, so this is not a server
regression.
