# Serve worker loop

`issuekit serve` turns a registered checkout into a long-running worker. It
polls the configured API project, pulls one unit of work at a time, launches
this checkout's configured agent, and records the result through the normal
lifecycle commands.

Nothing is pushed to the worker. The API server never dispatches work; each
serve process decides for itself when to ask for the next item. One serve
process serves exactly one checkout, one agent, and one role, so a machine that
implements and reviews runs two serve processes from two registered checkouts.

## Prerequisites

- The checkout is registered: `issuekit add` wrote `issuekit.local.toml` and
  published the worker to the API catalog. Serve refuses to start otherwise.
- An implementer resolves: `--agent`, `default_implementer`, or exactly one
  enabled assignee. See [Configuration](configuration.md).
- The checkout sits on the configured `work_branch` (or the run passes
  `--allow-any-branch`), and the tree is clean enough to pass the claim-time
  sync guard (or the run passes `--no-sync`).

## Modes

The four modes are mutually exclusive. The implement pool is the default.

| Mode | Poll source | Agent work | Terminal call |
|------|-------------|------------|---------------|
| default | `claim_next` (implement pool) | implement the claimed issue | `submit_for_review` |
| `--review` | `next_review` (review pool) | review the submitted issue | `approve` or `request_changes` |
| `--proposal-checks` | proposal checks addressed to this worker | verify the claim against the code | post the check result |
| `--triage` | the incoming proposal inbox | adopt matching proposals (or run the triage author agent) | issue creation |

`--triage` layers onto the implement loop: each poll first drains the inbox,
then attempts a claim. `[triage] auto_adopt = true` enables the same behavior
without the flag. When `[triage] author_agent` is set, the triage step runs that
agent instead of the mechanical auto-adopt.

```console
$ issuekit serve --agent codex                    # implementer worker
$ issuekit serve --agent claude --review          # reviewer worker
$ issuekit serve --agent claude --proposal-checks # proposal-check worker
$ issuekit serve --agent codex --triage           # implementer that also triages the inbox
```

## What one cycle does

1. **Startup recovery.** Before the first poll, the implement loop looks for
   issues still at `stage=implementing` that are held by this worker's keys and
   finishes them. A serve process killed mid-run resumes its own work instead of
   leaving an orphaned claim behind; see
   [Orphaned claim detection](orphaned-claim-detection.md).
2. **Poll.** One call to the pool. No work means an `idle` log line and a sleep
   of `--interval` seconds (default 15).
3. **Claim.** The claim applies the work-branch and clean-checkout guards and
   records this checkout as the holding worker.
4. **Run.** The agent runs under `--timeout-sec` (default 1800). Review feedback
   already on the issue body is re-injected into the prompt, so an issue
   returned at `stage=changes_requested` is picked up by the same loop without
   any extra step.
5. **Submit or decide.** On success the loop calls the lifecycle mutation for
   its mode and logs `submitted` or `reviewed`. On failure it logs `run_failed`
   and backs off.

## Backoff, limits, and exit

Errors use exponential backoff starting at 1s and capped at 60s; any success
resets it. Idle polls always wait `--interval`, not the backoff.

- `--once` attempts a single poll and exits. Useful for cron-style operation and
  for testing.
- `--max-issues <n>` exits after `n` successful submissions.
- `--priority high|medium|low` narrows the implement pool.
- `--model` and `--reasoning-effort` apply to every agent this loop launches.
  For mixed-agent setups prefer per-agent config or `[agents.<name>.roles.<role>]`
  overlays.

## Concurrency, logging, and shutdown

Serve takes a PID lock at `.agent-runs/serve.lock`. A second serve in the same
checkout exits with `issuekit serve is already running for this checkout (pid N)`.
A lock left behind by a dead process is detected and reclaimed, so a crashed
serve does not need manual cleanup.

Every event is written both to stderr and to `.agent-runs/serve.log` as a single
line of `key=value` pairs:

```
ts=2026-07-28T09:14:02 event=claimed issue=318 agent=codex
ts=2026-07-28T09:31:47 event=submitted issue=318 assignee= stage=review count=1
```

Useful event names: `idle`, `claimed`, `recovered`, `submitted`, `reviewed`,
`review_decision_discarded`, `run_error`, `run_failed`, `claim_error`,
`review_poll_error`, `triage_error`, `worker_registry_error`, `signal`, `stopped`.

Shutdown is two-stage. The first `SIGINT`/`SIGTERM` requests a graceful stop:
the current agent run finishes and the loop exits afterwards. A second signal
sets the abort flag and interrupts the running agent. Serve exits 0 on a
graceful stop.

## Worker heartbeat

While serve runs, a daemon thread re-publishes this worker to the API catalog
every `worker_heartbeat_interval_sec` seconds (default 60), refreshing
`last_seen`; `--heartbeat-interval <seconds>` overrides the configured value.
That heartbeat is what keeps `issuekit orphans` from flagging a long agent run
as stale, and what keeps `issuekit workers prune` (default staleness 300s) from
removing a live but idle worker. Staleness windows must be several heartbeat
periods wide.

The heartbeat is best-effort. If publishing fails, serve logs
`worker_registry_error` and keeps polling; it does not stop or retry faster.
When a worker disappears from `issuekit workers` while its serve process is
still alive, check the log for that event before re-registering.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `This checkout is not registered as an issuekit worker.` | no `issuekit.local.toml` | run `issuekit add` |
| `No implementer is configured.` | several enabled assignees, no default | pass `--agent` or set `default_implementer` |
| `issuekit serve is already running for this checkout` | live PID holds the lock | stop the other process, or serve from a second checkout |
| Repeated `claim_error` with growing backoff | API unreachable or auth expired | check `issuekit info --json`, re-authenticate |
| Repeated `run_failed` | the agent exits non-zero | read the run logs under `.agent-runs/` |
| `review_decision_discarded` with growing backoff | the reviewer agent emitted an unparseable review block, so the verdict was dropped | rerun the review and read the agent log under `.agent-runs/` |
| Work-branch guard blocks every claim | checkout is off `work_branch` | switch branches, or `--allow-any-branch` for human recovery |

## Related

- [Commands](commands.md) for the full flag list.
- [Configuration](configuration.md) for agents, roles, triage, and work-branch
  settings.
- [Registry maintenance](registry-maintenance.md) for removing and pruning
  workers.
- [Orphaned claim detection](orphaned-claim-detection.md) for claims a dead
  serve left behind.
- [`issuekit/agentrun/README.md`](../../issuekit/agentrun/README.md) for the
  agent runtime boundary serve launches through.
