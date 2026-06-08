---
origin: py_cr_wrapper#0@3f071a8
to: issuekit
reply_to: 
created: 2026-06-08
title: Improve issuekit implement progress visibility
---

# Proposal: Improve issuekit implement progress visibility

## Problem

While using `issuekit implement 127 --agent kimi --timeout-sec 1800` from the
py_cr_wrapper repository, the command completed successfully, but the waiting
experience was difficult to monitor.

Observed behavior:

- The `issuekit implement` terminal output stayed empty for long periods while
  the agent was actively working.
- Useful progress was written to `.agent-runs/<run>.err.log`, but the command
  itself did not surface that progress.
- The operator had to run separate commands such as `issuekit runs`,
  `tail .agent-runs/<run>.err.log`, and `git status --short` to distinguish
  active work from a stalled run.
- The run completion output reported `submitted_review ... stage=review`, but
  `issuekit info` listed the issue as `[in_progress]`. The frontmatter had
  `status: in_progress` and `stage: review`, so this may be technically
  correct, but the queue view was confusing.
- The agent work log is stored in `err.log` even when it contains normal
  progress notes rather than errors.
- `.agent-runs/` appeared as an untracked path after the run. That may be
  intended, but the command output does not explain whether it should be
  committed or ignored.

## Proposed improvements

1. Add a progress-following mode for `issuekit implement`, such as `--follow`,
   that streams or periodically summarizes the agent log.
2. Emit a heartbeat while the implement command is still running, for example elapsed time, run id, last log update time, and the most recent non-empty log line.
3. Show changed files periodically or at least at completion, so the operator can see that implementation is making progress.
4. Include both `status` and `stage` in `issuekit info` active issue listings,
   for example `[in_progress, stage=review]`.
5. Consider renaming normal agent progress logs from `err.log` to `agent.log`,
   `trace.log`, or another non-error name, while keeping real stderr separately
   if needed.
6. When `.agent-runs/` is created, print a short note explaining whether it is a
   run artifact and whether it is normally committed.

## Expected outcome

Operators waiting on `issuekit implement` can tell whether the agent is still
working, testing, stuck, or finished without opening multiple side terminals.
The command remains usable for long-running implementations and review
handoffs.
