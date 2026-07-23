# Registry maintenance

Use `issuekit workers remove <worker.repo>` to delete a known stale checkout
registration. The command accepts the current dotted key and the
machine-qualified `worker.repo@machine` address, prints the worker status,
`last_seen`, and any implementing issue it found, and refuses to delete an
implementing holder unless `--force` is passed.

Use `issuekit workers prune --dry-run` to review cleanup candidates before
deleting anything. Prune only offers workers whose `last_seen` heartbeat is
older than `--stale-after-sec`, that hold no implementing issue, and that are
not the `target_worker` of directed work. Without `--dry-run`, the command asks
you to type the candidate count before it deletes the workers.
`last_seen` only refreshes while `serve` or the worker heartbeat is running, so
quiet but live checkouts can look stale; start with `--dry-run` and use a
generous `--stale-after-sec` before deleting.

If prune skips a stale worker because an issue is still directed to it, use
`issuekit readdress <id>` first to return that issue to the repo pool. If prune
skips a stale worker because it holds an implementing claim, inspect
`issuekit orphans` and use `issuekit reclaim <id>` when the claim is truly
orphaned. See [Orphaned claim detection](orphaned-claim-detection.md).

Use `issuekit repos remove <repo>` only after worker cleanup. Catalog-aware API
servers return a conflict when a repo still has worker, issue, proposal, or
other references, and issuekit prints the reference counts returned by the API.
