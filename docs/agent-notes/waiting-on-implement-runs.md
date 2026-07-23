# Waiting on issuekit implement runs

**Applies to:** `issuekit implement <id> --agent <agent> --timeout-sec <n>`

An `implement` run launches a configured agent CLI as a subprocess and blocks
until that agent finishes or the timeout expires. Wait for it in the
foreground.

Backgrounding the command and watching for a completion signal does not work
reliably: the wrapper's exit is what carries the result, and the run artifacts
under `.agent-runs/` are only complete once the process has exited. Poll the
foreground command instead, with `--timeout-sec` set generously enough for the
agent to finish the work.

If a run does die without submitting, the claim is left at
`stage=implementing`. Recover it with `issuekit orphans` and
`issuekit reclaim <id>` - see
[../guides/orphaned-claim-detection.md](../guides/orphaned-claim-detection.md).
