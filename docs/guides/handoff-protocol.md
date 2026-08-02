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

Implementation runs provide a report file for facts known only to the
implementer, such as which permitted approach it chose or which environment it
verified. Authors may request those details in the issue body. Issuekit appends
the agent's closing implementation and verification report under `Implementer
report:` in the submit summary after sanitizing it to ASCII and capping it at
4000 characters.

The protocol text itself is generated from
[`issuekit/prompts/protocol.py`](../../issuekit/prompts/protocol.py) and
[`issuekit/prompts/spec.py`](../../issuekit/prompts/spec.py). Edit those
modules, not this guide, to change the protocol.

See also [Separation-of-duties guards](separation-of-duties.md) for the guards
that enforce the handoff boundaries.
