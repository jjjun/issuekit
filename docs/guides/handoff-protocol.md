# Handoff protocol

The role-based author, implementer, reviewer, triage, and pm protocol is
centralized in issuekit:

```console
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

The MCP server instructions are a short pointer to the full protocol. Read the
steps for one role with MCP `get_protocol(role=...)` or `issuekit protocol
--role <role>`; use `issuekit protocol` to read every role. Consuming repos
should reference these sources instead of copying the steps.

Implementation runs provide a report file for facts known only to the
implementer, such as which permitted approach it chose or which environment it
verified. Authors may request those details in the issue body. Issuekit appends
the agent's closing implementation and verification report under `Implementer
report:` in the submit summary after sanitizing it to ASCII and capping it at
4000 characters. A run that leaves the report missing or blank is not submitted
(`reason=missing_report`); pass `--allow-missing-report` to `issuekit implement`
to submit anyway once the gap is understood.

For code changes, the recommended flow is a single-checkout orchestration: run
`issuekit implement`, review the unstaged changes in that checkout with
`issuekit review`, then have the approving or orchestrating session commit the
approved changes with the issue ref in the commit message. A separate
`serve --review` checkout can review only committed and pushed changes it can
see, or evidence-only host and verification submissions.

The protocol text itself is generated from
[`issuekit/prompts/protocol.py`](../../issuekit/prompts/protocol.py), which
embeds the separation-of-duties table from
[`issuekit/guards/separation.py`](../../issuekit/guards/separation.py). Edit
those modules, not this guide, to change the protocol.

See also [Separation-of-duties guards](separation-of-duties.md) for the guards
that enforce the handoff boundaries.
