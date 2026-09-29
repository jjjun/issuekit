# Directed addressing

Issuekit keeps three axes separate:

- `repo` / `project`: the API issue or proposal namespace, such as `mine-py`.
- `worker`: a registered checkout inside that repo, displayed as
  `worker.repo`, such as `prod.mine-py`.
- `agent` / `assignee`: the model or human role that implements or reviews
  work, such as `codex` or `claude`.

Most work should target the repo pool. For example, `issuekit propose --to
mine-py ...` lets any eligible worker registered for `mine-py` claim the
resulting work. When a target repo has opted a checkout into directed work, use
`worker.repo` to address that one checkout:

```console
$ issuekit propose --to prod.mine-py --title "Patch production profile" --body "..."
```

The same worker name can exist on several machines (for example a
provisioning-created devenv `alpha.mine-py` on both `pike3` and `main1`). To
address exactly one of them, append the machine id in the canonical
machine-qualified form `worker.repo@machine`:

```console
$ issuekit propose --to alpha.mine-py@pike3 --title "Patch pike3 profile" --body "..."
```

`issuekit workers` prints the bare `worker.repo` on each worker's heading line
and the machine-qualified form in the `address=` detail line below it, so
callers know the exact string to direct to. A machine-qualified target only
matches a claiming worker on that machine, while the bare `worker.repo` form
stays machine-agnostic; the API rejects a bare directed target as ambiguous
when the same worker name is registered on multiple machines.

The dotted form is client-side sugar. Issuekit validates each token, sends the
repo/project separately from the worker name, and claim requests include the
local machine-qualified `worker.repo@machine` key so the API can hide work
directed to other workers or other machines.

Opting a checkout into directed work is a configuration decision; see
`worker_accept_directed` in [Configuration](configuration.md). Registration
(`issuekit add` and every `serve` heartbeat) sends `accept_directed` only when
that setting is true, and the API leaves the stored flag unchanged when the
field is absent. Setting `worker_accept_directed = false` later therefore does
not revoke an earlier opt-in, and no issuekit command sends
`accept_directed = false`. The only client-side way to clear the flag is to
re-create the registration: stop any `serve` in that checkout (its heartbeat
re-registers with the settings it started with), set
`worker_accept_directed = false`, run
`issuekit workers remove <worker.repo@machine>`, then run `issuekit add`. A new
registration starts with directed work disabled. The API checks the flag only
when a target is set, so issues already directed to the worker keep their
target until you readdress them.

To direct a new or existing issue, use:

```console
$ issuekit author --title "Verify production" --body-file issue.md --agent codex --target-worker prod.mine-py@main1
$ issuekit dispatch 42 --target-worker prod.mine-py@main1
```

Both commands validate the address against the registered worker catalog and
print the worker identity returned by the API. Use
`--allow-unregistered-worker` only when intentionally directing work to a
checkout that has not registered yet. Assignment selects the implementing
agent; direction selects the checkout where that agent must run.

`dispatch` also accepts `--assignee <agent>` to set the implementer in the same
call (omitted, the assignee is left unchanged) and `--stage todo|planned` for
the ready stage the directed issue lands in. Without `--stage` the API moves the
issue to `todo`. Only issues at `planned`, `todo`, or `changes_requested` can
be dispatched.

To clear a directed target and return an issue to the repo pool, use
`issuekit readdress <id>`. `--reason <text>` records an optional ASCII audit
reason with the readdress event. An issue with no target fails with
`Issue #<id> is not directed to a worker.` See
[Orphaned claim detection](orphaned-claim-detection.md).
