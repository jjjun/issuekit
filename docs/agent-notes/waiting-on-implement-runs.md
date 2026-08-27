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

## Output contract for orchestrators

`issuekit implement` always prints a `post_run` line to stdout as the last
line of output, regardless of outcome. That is the single line an
orchestrator should branch on:

    post_run id=<id> stage=<stage> submitted=<true|false> agent_exit=<n> cli_exit=<n>

- `agent_exit` is the agent subprocess exit code; `cli_exit` is the
  `issuekit implement` process exit code (also the process's real exit
  status). Do not confuse this with the run report's `agent_exit_code=`
  field printed earlier in the run (renamed from `exit_code=` - update any
  existing grep for that field name).
- `stage` is the issue's stage after the run: `review` on a successful
  submit, or whatever stage the issue was left at otherwise. It can be
  `unknown` if the post-run stage lookup itself fails (for example the API
  is unreachable) - this does not change `cli_exit`, which still reflects
  the original failure.
- Whenever the issue did not reach `stage=review`, a `not_submitted` line
  precedes `post_run`:

      not_submitted id=<id> stage=<stage> reason=<reason>

  `reason` is one of: `timed_out`, `agent_failed`, `no_changes`,
  `mojibake_gate`, `submit_error:<message>`, `run_error:<message>`.
  `submit_error` means the agent run finished and `submit_for_review` (or a
  guard around it) failed; `run_error` means the agent run itself never
  completed.
