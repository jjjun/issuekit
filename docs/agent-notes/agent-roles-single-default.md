# One agent maps to one default protocol role

**Applies to:** `issuekit protocol --agent <agent>`, MCP `get_protocol(agent=...)`,
and the `[agent_roles]` config table

`[agent_roles]` assigns each agent exactly one default role. There is no way to
give one agent two default roles. When a single agent name serves more than one
role - the claude-only setup, where claude implements and also reviews - only
the configured role is returned by `--agent`, and the other role is silently
wrong.

Reproduced with `[agent_roles] claude = "implementer"`:

```
issuekit protocol --agent claude    -> Handoff protocol (implementer)
issuekit protocol --role reviewer   -> Handoff protocol (reviewer)
```

The failure is quiet. A claude session launched as a reviewer by
`issuekit review <id> --agent claude` follows CLAUDE.md, runs
`issuekit protocol --agent claude`, and receives the implementer protocol. It
then tries to `claim_next_task` instead of `next_review`, because the steps it
was handed are the wrong ones. Nothing errors and nothing warns.

Rules that avoid it:

- When you know your role, pass `--role` / `role=` rather than `--agent`. Role
  always wins over the agent default, so this is correct under every
  configuration and is the safer habit even when `[agent_roles]` is unset.
- Only rely on `--agent` when one agent maps to one role for the whole
  workflow.
- Check `issuekit info` to see which role each agent currently resolves to
  before trusting `--agent`. The `Agent roles` section prints the effective
  mapping, including the built-in defaults.

Note that role-scoped model overlays (`[agents.<name>.roles.<role>]`) do not
have this problem. Those are resolved from the launch site - `issuekit review`
passes `reviewer`, `issuekit implement` passes `implementer` - so the correct
model and effort are selected regardless of what `[agent_roles]` says.
`[agent_roles]` only selects protocol text.
