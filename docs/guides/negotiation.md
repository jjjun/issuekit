# Cross-project negotiation

Use `issuekit negotiate` when two projects need to settle an interface before
either project can be specified independently. It runs a bounded,
agent-driven conversation between a frontend side and a backend side. Once the
thread agrees on a contract, `--finalize` creates cross-linked implementation
issues for both projects.

Use `issuekit propose` instead when the change belongs to another project and
you can already specify the requested work. Negotiation is for an undecided
shared interface, not a replacement for a well-scoped proposal.

Negotiation is CLI-only because it launches multiple long-running agent turns;
holding an MCP stdio transport open for that orchestration is fragile. The MCP
server does provide the read-only `list_negotiation_threads` tool for inspecting
persisted thread state.

## Start a thread

Start from an existing issue in the initiating project and choose configured
agents for each side:

```powershell
issuekit negotiate --from-issue <id> --to <project> --frontend-agent <agent> --backend-agent <agent>
```

The frontend agent runs in the initiating checkout. When a configured ref's
checkout declares the target project, the backend agent uses that checkout
automatically. Use `--backend-ref <ref>` to select a specific counterpart
checkout instead; it must declare the same project as `--to`:

```powershell
issuekit negotiate --from-issue <id> --to <project> --frontend-agent <agent> --backend-agent <agent> --backend-ref <ref>
```

The initiating checkout still supplies the configuration for the thread,
agent selection, and issues created by finalization. The backend ref is only
the backend agent's inspection directory; its checkout configuration is read
only to verify its declared project, and its worker identity is not loaded. The
backend-ref checkout must be clean before the run starts.

Both sides are read-only. If either turn modifies its worktree, issuekit
discards that turn's output and the run fails.

## Verdicts and thread status

Each entry has one of these verdicts:

- `propose`
- `counter`
- `agree`
- `blocked`

A thread's status is `negotiating`, `agreed`, or `blocked`. Any `blocked`
entry makes the thread blocked.

A thread becomes agreed only when the contract text matches after normalization,
not merely because both sides chose `agree`. It converges in either of these
cases:

- The latest entry is `agree` and an earlier entry from the other side has the
  identical contract.
- Both sides have an `agree` entry and every agreed contract hash is identical.

## Rounds and escalation

By default, a run allows four total agent turns (`--max-rounds 4`) and gives
each turn 120 seconds (`--timeout-sec 120`). If the turn budget is exhausted
while the thread remains `negotiating`, the result is `outcome=escalate`.
Escalation is a stop, not a failure; normally rerun with a larger
`--max-rounds` value.

## Inspect and finalize

Use `issuekit threads` to list negotiation threads, or pass a thread id to
inspect its entries and current outcome:

```powershell
issuekit threads
issuekit threads <thread_id>
issuekit threads --status agreed
```

`threads --status` filters the listed threads by their stored thread status:
`negotiating`, `agreed`, or `blocked`.

After a thread is agreed, finalize it with the target project:

```powershell
issuekit negotiate --finalize <thread_id> --to <project> --author-agent <agent> --priority medium
```

Finalization creates and cross-links implementation issues in the initiating
and target projects. It refuses threads that are not agreed. The author agent
is resolved from `--author-agent`, then `default_implementer`, then a single
enabled assignee. `--priority` controls the priority of the created issues.

## Mock mode

`--mock` uses `MockNegotiationStore`, persisted at
`.agent-runs/negotiations/mock.json`, and `MockIssueCreator`. It prevents API
proposals and issues from being created.

Mock mode does not mock the agents. The configured agent CLIs still run and
consume real tokens, so it is not a dry run.
