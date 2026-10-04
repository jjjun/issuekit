# Cross-project negotiation

Use `issuekit negotiate` when two projects need to settle an interface before
either project can be specified independently. It runs a bounded,
agent-driven conversation between a provider side and a consumer side. Once the
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

Start from an existing issue in the initiating project, declare whether that
project provides or consumes the contract, and choose configured agents for
each role:

```powershell
issuekit negotiate --from-issue <id> --to <project> --initiator-side consumer --provider-agent <agent> --consumer-agent <agent>
```

Use `--initiator-side provider` when the initiating project owns and exposes
the contract; use `consumer` when it integrates against the contract. The
initiator always opens the negotiation. When a configured ref's checkout
declares the target project, the counterpart agent uses that checkout
automatically. Use `--counterpart-ref <ref>` to select a specific counterpart
checkout instead; it must declare the same project as `--to`:

```powershell
issuekit negotiate --from-issue <id> --to <project> --initiator-side provider --provider-agent <agent> --consumer-agent <agent> --counterpart-ref <ref>
```

`--model` and `--reasoning-effort` apply to both agents for the run, and
`--json` prints the result as JSON.

The initiating checkout still supplies the configuration for the thread,
agent selection, and issues created by finalization. The counterpart ref is only
the counterpart agent's inspection directory; its checkout configuration is read
only to verify its declared project, and its worker identity is not loaded. An
explicit `--counterpart-ref` must point to a clean checkout, or the command
fails. When no ref declares the target project, the counterpart agent inspects
the initiating checkout instead. The same fallback applies when the
automatically picked ref's checkout is dirty; issuekit prints a warning and
ignores that ref.

Both agents are instructed to inspect their checkout read-only. As a backstop,
issuekit discards a turn's output when it leaves worktree changes, changes Git
config or hook/info files, changes local credential or agent settings, or moves
HEAD or the current branch; this includes a turn that commits its changes. The
check does not detect pushes, API or other external side effects, or edits that
the turn reverts before it finishes, so the prompt is the primary control.

## Verdicts and thread status

Each entry has one of these verdicts:

- `propose`
- `counter`
- `agree`
- `blocked`

A thread's status is `negotiating`, `agreed`, `blocked`, or `cancelled`. Any
`blocked` entry makes the thread blocked. `--cancel` requires `--to <project>`
and sends the `cancelled` status to that project's API. It works for
`negotiating` and `blocked` threads. For an issue-seeded thread, use the
initiating project. The API refuses `agreed` and already-cancelled threads with
`already_decided`. Cancelling changes only the thread row; pending turn
proposals remain pending, so issuekit discards them separately.

```powershell
issuekit negotiate --cancel <thread_id> --to <initiating-project>
```

A thread becomes agreed only when the contract text matches after normalization,
not merely because both sides chose `agree`. It converges in either of these
cases:

- The latest entry is `agree` and an earlier entry from the other side has the
  identical contract.
- Each side has an `agree` entry with a contract, and the latest such contract
  from each side is identical. Earlier `agree` entries are not compared.

## Rounds and escalation

`--max-rounds` (default 4) caps the total number of turns stored in the
thread, counted across every invocation and including the opening turn; it is
not a per-invocation budget. Each turn gets 120 seconds by default
(`--timeout-sec 120`). If the thread reaches the cap while it remains
`negotiating`, the result is `outcome=escalate`. Escalation is a stop, not a
failure; rerun with a `--max-rounds` value larger than the current number of
turns, because rerunning with the same value runs no turns and escalates again.

Each side keeps one agent session for the whole run when its agent can
continue a session, so later rounds resume the session the side opened on its
first round instead of exploring the repository again from a cold start. An
agent qualifies when its configuration sets `resumable`, `session_flag`, and
`resume_flag`; the built-in Claude config does, and Codex does not. Sessions
last for one `issuekit negotiate` invocation: resuming a thread in a later
invocation starts new sessions, because thread storage does not record session
ids and the counterpart side may run on another machine. The round prompt is
unchanged either way, so a side that cannot resume behaves exactly as before.

## Resuming and failed turns

Rerunning the same command continues the existing thread:

- With `--from-issue`, issuekit looks only at `negotiating` threads and resumes
  the one whose entries came from that issue. If several match, the command
  fails and asks you to inspect them with `issuekit threads`.

A rerun must pass the same `--initiator-side` as the run that opened the
thread; otherwise it fails.

If an agent turn times out, exits non-zero, returns output that cannot be
parsed, or changes the repository, the command exits 1 without storing that
turn. The thread stays `negotiating`; rerun the same command to continue it.

## Inspect and finalize

Use `issuekit threads` to list negotiation threads, or pass a thread id to
inspect its entries and current outcome:

```powershell
issuekit threads
issuekit threads <thread_id>
issuekit threads --status agreed
issuekit threads --mock
```

`threads --status` filters the listed threads by their stored thread status:
`negotiating`, `agreed`, `blocked`, or `cancelled`.
Use `threads --mock` to inspect the local mock negotiation store.

`threads` and the MCP `list_negotiation_threads` tool read only the current
project's thread store. Issue-seeded threads are stored in the initiating
project, so inspect them from that project's checkout.

After a thread is agreed, finalize it with the target project:

```powershell
issuekit negotiate --finalize <thread_id> --to <project> --author-agent <agent> --priority medium
```

Finalization creates and cross-links provider and consumer implementation
issues, and the consumer issue depends on the provider issue. Rerunning
`--finalize` on a finalized thread returns the existing refs. It refuses
threads that are not agreed. The author agent is resolved from
`--author-agent`, then `default_implementer`, then a single enabled assignee.
`--priority` controls the priority of the created issues.

## Mock mode

`--mock` uses `MockNegotiationStore`, persisted at
`.agent-runs/negotiations/mock.json`, and `MockIssueCreator`. It prevents API
proposals and issues from being created. `--cancel` requires the API store and
rejects `--mock`.

Mock mode does not mock the agents. The configured agent CLIs still run and
consume real tokens, so it is not a dry run.
