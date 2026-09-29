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

A pending proposal authored by the current project can seed the thread instead.
The proposal target is inferred from its qualified ref, and the initiating
project must be the consumer because the target proposal becomes the provider
issue after agreement:

```powershell
issuekit negotiate --from-proposal <project>#proposal:<id> --initiator-side consumer --provider-agent <agent> --consumer-agent <agent>
```

Starting this path atomically links and locks the pending proposal so inbox
triage cannot adopt duplicate provider work. A blocked proposal negotiation
remains linked and recoverable. To abandon it and return the proposal to normal
pending triage, cancel the thread explicitly:

```powershell
issuekit negotiate --cancel <thread_id> --from-proposal <project>#proposal:<id>
```

`--cancel` also accepts `--to <project>` in place of the proposal ref; either
way it cancels the thread in the target project's thread store.

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
issuekit discards a turn's output when it leaves worktree changes or moves HEAD
or the current branch; this includes a turn that commits its changes. The check
does not detect pushes, API or other external side effects, or edits that the
turn reverts before it finishes, so the prompt is the primary control.

## Verdicts and thread status

Each entry has one of these verdicts:

- `propose`
- `counter`
- `agree`
- `blocked`

A thread's status is `negotiating`, `agreed`, `blocked`, or `cancelled`. Any
`blocked` entry makes the thread blocked. `--cancel` moves a thread that is
still `negotiating` to `cancelled` in the store of the project named by `--to`
or the `--from-proposal` ref. It is meant for proposal-seeded threads, which
live in the target project's store; an issue-seeded thread lives in the
initiating project's store, so cancelling one takes `--to <initiating-project>`.

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
- With `--from-proposal`, issuekit reuses the thread linked to the proposal. If
  that thread is already `agreed` or `blocked`, the command returns the stored
  outcome without running agents. A `cancelled` thread cannot be resumed.

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
```

`threads --status` filters the listed threads by their stored thread status:
`negotiating`, `agreed`, `blocked`, or `cancelled`.

`threads` and the MCP `list_negotiation_threads` tool read only the current
project's thread store. Issue-seeded threads are stored in the initiating
project, but proposal-seeded threads are stored in the target project, so
inspect those from a checkout of the target project.

After a thread is agreed, finalize it with the target project:

```powershell
issuekit negotiate --finalize <thread_id> --to <project> --author-agent <agent> --priority medium
```

For a proposal-seeded thread, pass the proposal ref again so issuekit selects
the target project's thread store:

```powershell
issuekit negotiate --finalize <thread_id> --from-proposal <project>#proposal:<id> --author-agent <agent> --priority medium
```

Finalization creates and cross-links provider and consumer implementation
issues, and the consumer issue depends on the provider issue. For an
issue-seeded thread, issuekit creates both issues. For a proposal-seeded
thread, the API atomically adopts the source as the provider issue and creates
or reuses the dependent consumer issue. Either way, rerunning `--finalize` on a
finalized thread returns the existing refs. It refuses threads that are not
agreed. The author agent is resolved from `--author-agent`, then
`default_implementer`, then a single enabled assignee. `--priority` controls
the priority of the created issues.

## Mock mode

`--mock` uses `MockNegotiationStore`, persisted at
`.agent-runs/negotiations/mock.json`, and `MockIssueCreator`. It prevents API
proposals and issues from being created. `--cancel`, and running rounds with
`--from-proposal`, require the API store and reject `--mock`.

Mock mode does not mock the agents. The configured agent CLIs still run and
consume real tokens, so it is not a dry run.
