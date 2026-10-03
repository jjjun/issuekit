# Waiting on issuekit implement runs

**Applies to:** `issuekit implement <id> --agent <agent> --timeout-sec <n>`

An `implement` run launches a configured agent CLI as a subprocess, then
submits the result for review, and only then exits. Wait for the
`issuekit implement` process itself to exit, either in the foreground or in the
background with an exit notification, and branch on its final `post_run` line.
Real runs often outlast a foreground tool-call cap, so a background run with an
exit notification is usually the practical choice. The default
`--timeout-sec` is 600; raise it for larger issues.

Do not treat `.agent-runs/` or `issuekit runs` as the completion signal. The
runner writes the terminal status (for example `completed`) as soon as the
agent subprocess exits, before issuekit runs the submit step, so a completed
run status does not mean the issue was submitted.

If a run does die without submitting, the claim is left at
`stage=implementing`. Inspect `issuekit show <id> --json` for its `stale_run`
warning, then rerun `issuekit implement <id>` to recover it. `issuekit orphans`
and `issuekit reclaim` use API worker heartbeats; they do not inspect
`.agent-runs` and cannot identify a dead one-shot `implement` process. See
[../guides/orphaned-claim-detection.md](../guides/orphaned-claim-detection.md)
for worker-heartbeat detection.

## Output contract for orchestrators

Once the claim succeeds, `issuekit implement` prints a `post_run` line to
stdout as the last line of output, whatever the outcome. Failures before or
during the claim (no implementer resolves, the issue is not found, or a claim
guard or API error) exit 1 with only a stderr message and no `post_run` line.
Otherwise `post_run` is the single line an orchestrator should branch on:

    post_run id=<id> stage=<stage> submitted=<true|false> agent_exit=<n> cli_exit=<n>

- `agent_exit` is the agent subprocess exit code; `cli_exit` is the
  `issuekit implement` process exit code (also the process's real exit
  status). The run report's `agent_exit_code=` field is separate from this
  summary line.
- `stage` is the issue's stage after the run: `review` on a successful
  submit, or whatever stage the issue was left at otherwise. It can be
  `unknown` if the post-run stage lookup itself fails (for example the API
  is unreachable) - this does not change `cli_exit`, which still reflects
  the original failure.
- Whenever the issue did not reach `stage=review`, a `not_submitted` line
  precedes `post_run`:

      not_submitted id=<id> stage=<stage> reason=<reason>

  `reason` is one of: `timed_out`, `agent_failed`, `no_changes`,
  `mojibake_gate`, `missing_report`, `submit_error:<message>`,
  `run_error:<message>`.
  `missing_report` means the agent run finished but wrote no implementer
  report (or only a whitespace one); recover with `--allow-missing-report`
  once the missing verification is understood. `submit_error` means the
  agent run finished and `submit_for_review` (or a guard around it) failed;
  `run_error` means the agent run itself never completed.
- For `reason=no_changes` or `reason=missing_report`, two extra lines can
  follow `not_submitted`: a `final_message_tail=<text>` line with a bounded,
  whitespace-collapsed tail of the agent's last assistant message (present
  only when the run captured one), and a `HINT:` line noting that a common
  cause is a verification command started in the background whose completion
  never rejoined the turn, with a suggestion to retry `issuekit implement`.
  A `final_message_tail` ending in something like "I'll wait for the
  background run to finish; no need to poll" is the signature of that
  failure mode - the agent never restarted the run, so it exited with no
  diff.
- For `reason=no_changes` on a run that resumed over uncommitted edits left
  by an earlier attempt, the `HINT:` line differs: it says the worktree
  changes predate this run and suggests
  `issuekit implement <id> --allow-no-changes` to submit them if they are
  complete.
