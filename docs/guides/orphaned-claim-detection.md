# Orphaned claim detection

When an implementer session dies mid-turn it can leave an issue stuck at
`stage=implementing` with an `assignee` still set. Because the assignee is
populated, the pull-based pool never re-offers it, so no idle agent picks it
up and the issue silently stalls.

`issuekit orphans` surfaces these without out-of-band forensics. An implementer
claim records which worker checkout (`worker.repo`) holds the issue, and the
worker registry tracks each live checkout's `last_seen` heartbeat. The
command cross-references the two and flags an implementing issue when either:

- `no_worker`: no registered worker matches the claim's worker key, so the
  holder is gone; or
- `expired_heartbeat`: a matching worker exists but its last heartbeat is
  more than `--stale-after-sec` seconds old (default 300).

Machine-qualified worker keys (`worker.repo@machine`) match only a registry row
for that machine. Older claims that record only `worker.repo` remain
machine-agnostic and match registered checkouts with that worker and repo.

Directed but unclaimed work is also reported when its `target_worker` is gone
or stale, using `directed_no_worker` or `directed_expired_heartbeat`. These
issues are not implementing claims, but they will not return to the repo pool
until the directed target is cleared.

Two cases are never flagged, because there is no liveness signal to judge:

- An implementing issue with no recorded worker. `reclaim` refuses it unless
  you pass `--force`.
- An issue whose matching worker has a missing or unparseable `last_seen`.
  That worker counts as live.

```console
$ issuekit orphans
Orphaned or stale implementing claims: 1
- #168: ... [assignee=claude worker=issuekit.issuekit] (stale: no heartbeat since 2026-07-03T01:32:30Z)
```

The `last_seen` heartbeat is refreshed by the `issuekit serve` worker loop (and
on `issuekit add`), not by a one-shot `issuekit claim`/`issuekit implement`.
A long-running implementer run through `serve` heartbeats at the configured
interval (default 60s) and is not flagged; a manual one-shot implementer that
holds a claim without running `serve` may show as `expired_heartbeat`. Keep the
staleness window several heartbeat periods wide. `orphans` prints a warning
when `--stale-after-sec` is not wider than the configured
`worker_heartbeat_interval_sec`, since a healthy worker may then look stale
between beats.

## Recovery

Use `issuekit reclaim <id>` to return a listed stale claim to the implement
pool. The command re-checks `orphans` before calling the API and passes the
detected worker as a race guard, so a resumed holder is not overwritten. That
re-check uses `--stale-after-sec` (default 300), so pass the same window you
used with `orphans`. `--reason <text>` records an optional ASCII audit reason
with the reclaim event.

Use `--force` only for human emergency recovery when the staleness check should
be skipped. `--force` still sends the worker that held the issue when issuekit
read it, so it skips only the staleness check. If that worker resumes or
another worker takes the claim between the read and the reclaim request, the
API returns `race_lost` instead of overwriting the current holder. This keeps
the emergency path optimistic-concurrency safe; there is intentionally no
unconditional override flag. The one case where `--force` sends
`expected_worker=None` is an issue with no recorded worker, where there is no
holder to guard against; `--force` is the only way to reclaim such an issue,
because `orphans` never lists it.

Use `issuekit readdress <id>` to clear a directed `target_worker` and return
that issue to the repo pool. The command sends the target worker it observed as
a race guard, so if the API sees a different target by the time it handles the
request it rejects the update instead of clearing newer directed work.

## Killed one-shot implementer runs

`orphans`/`reclaim` judge liveness from the API worker heartbeat, so they do
not cover a one-shot `issuekit implement` killed before it could submit: the
issue stays at `stage=implementing` with an assignee, but no worker registry
entry ever goes stale. Instead, `.agent-runs/<run-id>.status.json` for that run
keeps its own heartbeat. A killed run's heartbeat goes stale and the record
reconciles to `status=abandoned` with `terminal_reason=heartbeat_lost`
(`ended_at` set from the last heartbeat) the next time `issuekit runs` reads
it. `issuekit show <id>` reports a dead run on an issue stuck at `implementing`
even before that reconciliation happens, and resuming with `issuekit implement
<id>` warns before spending an agent turn if the worktree already holds
unattributed changes from the dead run. See the recovery hint from that
warning, or `issuekit implement <id> --allow-no-changes`, to submit the
existing worktree changes once you have verified them.

```console
$ issuekit show 484 --json
...
"stale_run": "run=20260830-... heartbeat_at=... looks dead (heartbeat stale); ..."
```
