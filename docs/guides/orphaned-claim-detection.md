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
- `expired_heartbeat`: a matching worker exists but has not sent a heartbeat
  for at least `--stale-after-sec` seconds (default 300).

Directed but unclaimed work is also reported when its `target_worker` is gone
or stale, using `directed_no_worker` or `directed_expired_heartbeat`. These
issues are not implementing claims, but they will not return to the repo pool
until the directed target is cleared.

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
staleness window several heartbeat periods wide.

## Recovery

Use `issuekit reclaim <id>` to return a listed stale claim to the implement
pool. The command re-checks `orphans` before calling the API and passes the
detected worker as a race guard, so a resumed holder is not overwritten. Use
`--force` only for human emergency recovery when the staleness check should be
skipped. `--force` still sends the worker that held the issue when issuekit read
it, so it skips only the staleness check. If that worker resumes or another
worker takes the claim between the read and the reclaim request, the API returns
`race_lost` instead of overwriting the current holder. This keeps the emergency
path optimistic-concurrency safe; there is intentionally no unconditional
override flag that sends `expected_worker=None`.

Use `issuekit readdress <id>` to clear a directed `target_worker` and return
that issue to the repo pool. The command sends the target worker it observed as
a race guard, so if the API sees a different target by the time it handles the
request it rejects the update instead of clearing newer directed work.
