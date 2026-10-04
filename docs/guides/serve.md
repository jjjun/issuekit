# Serve worker loop

`issuekit serve` turns a registered checkout into a long-running worker. It
polls the configured API project, pulls one unit of work at a time, launches
this checkout's configured agent, and records the result through the normal
lifecycle commands.

Nothing is pushed to the worker. The API server never dispatches work; each
serve process decides for itself when to ask for the next item. A serve process
normally runs one agent in one role, so a machine that implements and reviews
runs two serve processes from two registered checkouts. With `--triage` and
`[triage] author_agent` configured, the implement loop also launches a second
agent for proposal triage.

## Prerequisites

- The checkout is registered: `issuekit add` wrote the worker identity to
  `issuekit.local.toml`, and `api_url` is configured. Serve checks only that
  local file and refuses to start without it; it does not check the API catalog.
  Its first heartbeat, sent at startup, publishes the worker, so a catalog
  update that failed during `issuekit add` is repaired once serve reaches the
  API.
- An agent resolves: `--agent`, then `default_implementer`, then exactly one
  enabled assignee. Every mode uses this order, including `--review` and
  `--proposal-checks`; `default_reviewer` is never consulted, so a reviewer
  worker should pass `--agent`. See [Configuration](configuration.md).
- The checkout sits on the configured `work_branch` (or the run passes
  `--allow-any-branch`), and the tree is clean enough to pass the claim-time
  sync guard (or the run passes `--no-sync`).
- No issue author guard is recorded for this checkout. Serve has no
  `--allow-author-session` override; see Troubleshooting. Setting
  `ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0` also bypasses this guard for serve and
  relaxes the server-side author-implementer guard.

## Modes

There are three modes. The implement pool is the default; `--review` and
`--proposal-checks` select the other two and cannot be combined. `--triage` is
not a separate mode but an add-on to the implement mode, and `--review` and
`--proposal-checks` reject both `--triage` and `--priority`.

| Mode | Poll source | Agent work | Terminal call |
|------|-------------|------------|---------------|
| default | `claim_next` (implement pool) | implement the claimed issue | `submit_for_review` |
| `--review` | `next_review` (review pool) | review the submitted issue | `approve` or `request_changes` |
| `--proposal-checks` | proposal checks addressed to this worker | verify the claim against the code | post the check result |
| `--triage` (implement add-on) | the incoming proposal inbox | adopt matching proposals (or run the triage author agent) | issue creation |

Review, proposal-check, and configured triage agents run through the read-only
evaluation guard. It removes API credentials from the child environment and
rejects changes to the worktree, Git config, hook and info files, and local
credential or agent settings.

`--triage` layers onto the implement loop: each poll adopts up to
`max_adoptions_per_cycle` matching proposals (default 5), then attempts a claim.
`[triage] auto_adopt = true` enables the same behavior without the flag. When
`[triage] author_agent` is set, the triage step runs that agent instead of the
mechanical auto-adopt. Automatically adopted issues are held at `planned` by
default, so the same poll cannot claim them. A human can release one with
`issuekit plan <id> --stage todo`. If a hold request fails, the poll stops
before claiming and retries the hold before work on its next poll.

`[triage] hold_auto_adopted = false` disables this release gate for mechanical,
triage-author, and proposal-check approvals. `trusted_origins` only filters
which proposals are eligible; it does not authenticate their sender until
issuekit#403 is implemented. Turning the hold off therefore allows a forged
origin to reach unattended implementation.

```console
$ issuekit serve --agent codex                    # implementer worker
$ issuekit serve --agent claude --review          # reviewer worker
$ issuekit serve --agent claude --proposal-checks # proposal-check worker
$ issuekit serve --agent codex --triage           # implementer that also triages the inbox
```

## Topologies

The recommended flow for code changes is a single-checkout orchestration: run
`issuekit implement <id> --agent <implementer>`, review in that checkout with
`issuekit review <id> --agent <reviewer>`, then commit the approved changes
with the issue ref in the commit message. A separate `serve --review` checkout
can review only committed and pushed changes it can see, or evidence-only host
and verification submissions. An implement-mode `serve` processes one issue at
a time and waits at the claim-sync guard until the submitted changes are
committed.

## What one cycle does

1. **Startup recovery.** Before the first poll, the implement loop looks for
   every issue still at `stage=implementing` held by this checkout's worker
   keys, including manual claims, and runs them with serve's `--agent` value,
   regardless of each issue's assignee. A serve process killed mid-run resumes
   its own work instead of leaving an orphaned claim behind; see
   [Orphaned claim detection](orphaned-claim-detection.md).
2. **Poll.** One call to the pool. No work means an `idle` log line
   (`review_idle` or `proposal_checks_idle` in the other modes) and a sleep of
   `--interval` seconds (default 15).
3. **Claim.** The claim applies the work-branch and clean-checkout guards and
   records this checkout as the holding worker.
4. **Run.** The agent runs under `--timeout-sec` (default 1800). Review feedback
   already on the issue body is re-injected into the prompt, so an issue
   returned at `stage=changes_requested` is picked up by the same loop without
   any extra step.
5. **Submit or decide.** On success the loop calls the lifecycle mutation for
   its mode and logs `submitted` or `reviewed`. On failure it logs `run_failed`
   (`review_failed` in review mode), or `run_error` (`review_error`) when the
   run raised an error, and backs off.

## Backoff, limits, and exit

Errors use exponential backoff starting at 1s and capped at 60s; any success
resets it. Idle polls always wait `--interval`, not the backoff.

- `--once` runs at most one agent and exits. Startup recovery takes precedence;
  otherwise the loop attempts one poll. Useful for cron-style operation and for
  testing.
- `--max-issues <n>` exits after `n` successful submissions, including issues
  finished by startup recovery. With `--review` it counts review decisions;
  `--proposal-checks` ignores it.
- `--proposal-check-limit <n>` caps how many pending proposal checks one
  `--proposal-checks` cycle evaluates (default 50, maximum 500). The option is
  ignored unless `--proposal-checks` is selected.
- `--priority high|medium|low` narrows the implement pool.
- `--model` and `--reasoning-effort` apply to every agent this loop launches.
  For mixed-agent setups prefer per-agent model and reasoning settings or
  `[agents.<name>.roles.<role>]` overlays. Keep executable, launch, and
  permission settings in machine config.

## Concurrency, logging, and shutdown

Serve takes a PID lock at `.agent-runs/serve.lock`. A second serve in the same
checkout exits with `issuekit serve is already running for this checkout (pid N)`.
A lock left behind by a dead process is detected and reclaimed, so a crashed
serve does not need manual cleanup.

Every event is written both to stderr and to `.agent-runs/serve.log` as a single
line of `key=value` pairs. Values containing whitespace or `=` are JSON-quoted
strings, so embedded newlines are escaped. The one exception is `signal`, which
goes to stderr only.

```
ts=2026-07-28T09:14:02 event=claimed issue=318 agent=codex
ts=2026-07-28T09:31:47 event=submitted issue=318 assignee= stage=review count=1
```

Event names by loop:

| Loop | Events |
|------|--------|
| any mode | `stopped`, `signal`, `worker_registry_error`, `worker_registry_escalated` |
| implement | `recovered`, `recovery_error`, `idle`, `claimed`, `claim_error`, `submitted`, `run_error`, `run_failed` |
| `--triage` | `auto_adopted`, `triage_adoption_error`, `triage_error`, and `triage_author_*` when `[triage] author_agent` is set |
| `--review` | `review_idle`, `reviewing`, `review_skipped`, `review_poll_error`, `reviewed`, `review_error`, `review_failed`, `review_decision_discarded` |
| `--proposal-checks` | `proposal_checks_idle`, `proposal_checks_cycle_start`, `proposal_checks_cycle_complete`, `proposal_checks_cycle_error`, `proposal_check_decision`, `proposal_check_already_decided`, `proposal_check_error` |

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
periods wide; serve prints a warning at startup when the heartbeat interval is
300s or more, because the default 300s staleness window is then no wider than
one period.

The heartbeat is best-effort. If publishing fails, serve logs
`worker_registry_error` with the consecutive failure count and the last
successful beat, and keeps polling; it does not retry faster. A
`worker_registry_error` with `consecutive=0` can also follow a successful beat
when a non-fatal operation fails, such as a missing repo endpoint or failed
profile push. Once failures have lasted longer than 300s (counted from the last
successful beat, or from the first failure if none succeeded), serve also logs
`worker_registry_escalated`, once per failure streak: from then on `orphans`
and `workers prune` may treat this worker as stale.

By default the heartbeat never stops serve. `--max-heartbeat-failures <n>`
(default 0, unlimited) requests a graceful stop after `n` consecutive failures:
the current agent run finishes and serve exits 0, as after a first signal.
When a worker disappears from `issuekit workers` while its serve process is
still alive, check the log for these events before re-registering.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `This checkout is not registered as an issuekit worker.` | no `issuekit.local.toml` | run `issuekit add` |
| `No implementer is configured.` | no enabled assignee, or several enabled assignees with no default | pass `--agent` or set `default_implementer` |
| `Agent preflight failed` | the selected agent has invalid runtime settings or its executable is unavailable | fix the agent configuration or install the executable before starting serve |
| `issuekit serve is already running for this checkout` | live PID holds the lock | stop the other process, or serve from a second checkout |
| Repeated `claim_error` with growing backoff | API unreachable or auth expired | check `issuekit info --json`, re-authenticate |
| `claim_error` with `Claim-sync guard blocks claim-next` | dirty working tree, or a failed `git status`, `git fetch`, or `git merge --ff-only` for `work_branch` | commit or stash, fix the Git failure, or pass `--no-sync`; see [Topologies](#topologies) |
| `claim_error` with `Author-session guard blocks claim-next` | this checkout recorded an issue author guard, which blocks every pool claim; serve has no `--allow-author-session` | hand off the authored issue, then run `issuekit author-guard clear` |
| Repeated `run_failed` | the agent exits non-zero; the claim stays at `implementing` under this worker, and serve does not retry it in the same process | read the run logs under `.agent-runs/`, then use `issuekit reclaim <id>` after the worker heartbeat is stale or `issuekit implement <id>` to recover it. `orphans` does not flag the claim while this worker keeps heartbeating |
| `review_decision_discarded` with growing backoff | the reviewer agent emitted an unparseable review block, so the verdict was dropped | read the reported parse error and stdout log, then rerun the review or use the printed manual `request-changes`/`approve` fallback |
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
