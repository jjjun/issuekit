# Handoff protocol

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

The protocol text itself is generated from
[`issuekit/prompts/protocol.py`](../../issuekit/prompts/protocol.py) and
[`issuekit/prompts/spec.py`](../../issuekit/prompts/spec.py). Edit those
modules, not this guide, to change the protocol.

See also [Separation-of-duties guards](separation-of-duties.md) for the guards
that enforce the handoff boundaries.
