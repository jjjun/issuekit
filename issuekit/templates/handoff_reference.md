## Handoff protocol

This repo uses the issuekit multi-agent handoff. For the current steps, run
`issuekit protocol --role <role>` (e.g. `author`, `implementer`, `reviewer`,
`triage`, or `pm`), or read the issuekit MCP server instructions /
`get_protocol` tool. `issuekit protocol --agent <agent>` prints the configured
default role for that agent.

Do not copy the steps here; issuekit is the source of truth. Launch your agent
from the repo root so the MCP server resolves the repo configuration.

If work originates in another project but belongs here, use the cross-project
proposal flow from the origin project. Do not create a local issue here unless
the protocol says the work is local to this repo.
