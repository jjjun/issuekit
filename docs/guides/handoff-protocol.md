# Handoff protocol

The role-based author, implementer, reviewer, triage, and pm protocol is
centralized in issuekit:

```powershell
issuekit protocol
issuekit protocol --agent codex
issuekit protocol --agent claude
issuekit protocol --agent kimi
issuekit protocol --role author
issuekit protocol --role implementer
issuekit protocol --role reviewer
issuekit protocol --role triage
issuekit protocol --role pm
```

The MCP server serves the full text through the `get_protocol` tool. Its server
instructions carry only the delegation cycle overview and a pointer to
`get_protocol`. Consuming repos should reference this command instead of
copying the steps.

Implementation runs provide a report file for facts known only to the
implementer, such as which permitted approach it chose or which environment it
verified. Authors may request those details in the issue body. Issuekit appends
the agent's closing implementation and verification report under `Implementer
report:` in the submit summary after sanitizing it to ASCII and capping it at
4000 characters. A run that leaves the report missing or blank is not submitted
(`reason=missing_report`); pass `--allow-missing-report` to `issuekit implement`
to submit anyway once the gap is understood.

The protocol text itself is generated from
[`issuekit/prompts/protocol.py`](../../issuekit/prompts/protocol.py), which
embeds the separation-of-duties table from
[`issuekit/guards/separation.py`](../../issuekit/guards/separation.py). Edit
those modules, not this guide, to change the protocol.

See also [Separation-of-duties guards](separation-of-duties.md) for the guards
that enforce the handoff boundaries.
