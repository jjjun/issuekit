# One agent maps to one default protocol role

**Applies to:** `issuekit protocol --agent <agent>`, MCP `get_protocol(agent=...)`,
and the `[agent_roles]` config table

The built-in defaults are `codex = "implementer"` and `claude = "reviewer"`;
`[agent_roles]` can override them. Use `issuekit protocol --role <role>` when
the role is known. This explicit form reliably selects the requested protocol,
regardless of the agent's configured default.
