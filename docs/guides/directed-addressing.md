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

`issuekit workers` prints each worker's machine-qualified address so callers
know the exact string to direct to. A machine-qualified target only matches a
claiming worker on that machine, while the bare `worker.repo` form stays
machine-agnostic; the API rejects a bare directed target as ambiguous when the
same worker name is registered on multiple machines.

The dotted form is client-side sugar. Issuekit validates each token, sends the
repo/project separately from the worker name, and claim requests include the
local machine-qualified `worker.repo@machine` key so the API can hide work
directed to other workers or other machines.

Opting a checkout into directed work is a configuration decision; see
`worker_accept_directed` in [Configuration](configuration.md). To clear a
directed target and return an issue to the repo pool, use `issuekit readdress
<id>`. To direct a new or existing issue, use:

```console
$ issuekit author --title "Verify production" --body-file issue.md --agent codex --target-worker prod.mine-py@main1
$ issuekit dispatch 42 --target-worker prod.mine-py@main1
```

Both commands validate the address against the registered worker catalog and
print the worker identity returned by the API. Use
`--allow-unregistered-worker` only when intentionally directing work to a
checkout that has not registered yet. Assignment selects the implementing
agent; direction selects the checkout where that agent must run. See
[Orphaned claim detection](orphaned-claim-detection.md).
