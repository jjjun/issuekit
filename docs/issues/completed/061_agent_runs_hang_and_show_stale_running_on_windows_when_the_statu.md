---
id: 61
status: completed
priority: high
created: 2026-06-17
completed: 2026-06-17
stage: done
origin: infra-toolkit#0@0c77cd2
title: Agent runs hang and show stale 'running' on Windows when the status-writer thread crashes (os.replace PermissionError)
---

# Issue #61: Agent runs hang and show stale 'running' on Windows when the status-writer thread crashes (os.replace PermissionError)

## Problem

Adopted from an incoming cross-project proposal.

## Proposed Solution

# Proposal: Agent runs hang and show stale 'running' on Windows when the status-writer thread crashes (os.replace PermissionError)

# Agent runs hang and show stale "running" on Windows when the status-writer thread crashes

## Summary

On Windows, `issuekit implement <id> --agent <agent>` runs frequently appear hung
and `issuekit runs` shows stale `running` entries with implausible elapsed times
(observed `running 4196s`, and a zombie `running 510337s` ~= 6 days). The root
cause is that the background status-writer thread dies with a Windows
`PermissionError` during the atomic status-file replace, after which heartbeats
freeze, `--follow` stops updating, and long agent runs never record completion or
submit for review even when the implementation finished on disk.

Reproduced repeatedly in the infra-toolkit repo (3 consecutive agent runs,
issues #144/#145/#146; all 3 stalled the same way). Work was complete each time;
we had to verify on disk and drive `submit-review`/`approve` manually.

## Observed failure

Traceback emitted by the runner while the agent subprocess was still working:

```
Exception in thread Thread-1 (_loop):
Traceback (most recent call last):
  ...
  File ".../issuekit/agents/runner.py", line 307, in _loop
    self._tick()
  File ".../issuekit/agents/runner.py", line 321, in _tick
    write_status(self.run_status_path, self.run_status)
  File ".../issuekit/agents/status.py", line 73, in write_status
    temp_path.replace(path)
  File ".../pathlib/_local.py", line 780, in replace
    os.replace(self, target)
PermissionError: [WinError 5] Access is denied:
  '...\.agent-runs\.20260617-155456.status.json.6932.tmp'
  -> '...\.agent-runs\20260617-155456.status.json'
```

After this, `<run>.status.json` `heartbeat_at`/`last_log_at` and the
`<run>.agent.log` mtime freeze at the crash time, while the agent keeps running
for many more minutes. `issuekit runs` then reports the frozen run as `running`
indefinitely.

## Why it happens

- `write_status` writes a temp file then `Path.replace()` (i.e. `os.replace`) to
  atomically swap it in. On Windows, `os.replace` raises `PermissionError`
  (WinError 5) or sometimes WinError 32 (sharing violation) if the destination
  is momentarily open by another handle. Likely concurrent openers: a reader such
  as `issuekit runs` or the `--follow` heartbeat reading `status.json`, an editor,
  or antivirus/Defender scanning the just-written temp file. Windows rename is far
  less tolerant than POSIX rename here.
- The status-writer runs in a daemon thread (`Thread-1 (_loop)`). When `_tick`
  raises, the thread dies and is never restarted, so all subsequent heartbeats are
  lost. The main thread keeps waiting on the agent subprocess, so the run looks
  alive to the OS but dead to issuekit.

## Impact

- Operators cannot tell whether a long run is progressing, hung, or finished.
- Long agent runs that execute a slow verification step never auto-submit. In our
  case the agent's final step was the full `uv run pytest` (~5-6 min; 332 tests,
  dominated by real-git suites). That long-but-healthy step is what kept the run
  busy long enough for the lost heartbeats to look like a hang.
- Stale/zombie `running` rows accumulate in `issuekit runs` and never clear.

## Suggested fixes (for issuekit to weigh)

1. Make `write_status` resilient instead of fatal:
   - Retry `os.replace` with short backoff on `PermissionError` (WinError 5/32),
     e.g. a few attempts over ~1s.
   - If replace still fails, fall back to a best-effort in-place write so the
     status is at least eventually consistent.
   - Never let the writer loop die: wrap `_tick` in try/except, log, and keep
     looping. A single failed status write must not silently kill heartbeats.

2. Reduce contention on the status file:
   - Open status readers (`issuekit runs`, `--follow`) with non-locking / share-
     delete semantics, or read a snapshot copy, so a concurrent read cannot block
     the atomic replace.

3. Detect and surface stale runs in `issuekit runs`:
   - Mark a run as `stale`/`likely-dead` (not `running`) when `heartbeat_at` is
     older than N x the heartbeat interval.
   - Reconcile using the recorded `pid`: if the pid is gone, transition the run
     to `failed`/`interrupted` and free any claim, so the issue does not stay
     wedged in `implementing`.

4. Robustness for long verification steps (secondary):
   - Consider a configurable/per-issue verification command, or guidance that the
     agent's final check need not be the entire slow suite, so a healthy long step
     is not mistaken for a hang. This is a mitigation, not the core fix.

## Environment

- Windows 11; issuekit installed as a uv tool
  (`C:\Users\jj\AppData\Roaming\uv\tools\issuekit`), Python 3.13.
- Agent: codex (codex-cli 0.139.0).
- The atomic-replace pattern in `issuekit/agents/status.py` `write_status`
  (around line 73) and the `_loop`/`_tick` thread in `issuekit/agents/runner.py`
  (around lines 307/321) are the relevant code paths.

## Impact

- Adopted proposal content should be reviewed locally.

## Implementation Plan

1. Triage the adopted proposal into local implementation steps.

## Test Plan

- Run the relevant local verification commands.

## Related Resources

- Origin: `infra-toolkit#0@0c77cd2`

## Review Feedback

- Changes requested. Full pytest suite passes (309 passed, 22 skipped) and all 5 changed files are LF/no-BOM (check-encoding clean). The core fix is correct: wrapping _tick() in try/except (runner.py:312-319) stops a single failed status write from killing the heartbeat thread, and the stale-marking in the runs table (table-only; JSON keeps raw "running") is well designed. One blocking issue must be fixed before approval:

BLOCKER -- write_status can still raise on genuine lock contention, defeating the final-status write.
In status.py:102-109 the in-place fallback is not protected:

    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass

The finally guards only unlink; path.write_text itself is unguarded. In the exact lock-contention case (destination held by another handle so os.replace fails), the in-place write to that same destination also fails with PermissionError and propagates out of write_status -- contradicting the docstring's "This never raises on lock contention".

Why it matters: the final terminal-status write at runner.py:507 runs on the main thread, OUTSIDE the _tick try/except. If write_status raises there, run() unwinds before returning AgentResult and submit-for-review never happens -- reproducing the exact issue #61 symptom ("finished on disk but never records completion / never submits"). The _tick guard does NOT cover this path.

Fix (small): swallow OSError around the fallback write too, so write_status genuinely never raises on IO contention. If the write fully fails the record stays "running" on disk, which the new is_stale safety net surfaces after STALE_AFTER_SEC -- a clean degraded outcome.

    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError:
        pass
    finally:
        ...

Also add a test covering this: existing test_write_status_does_not_raise_on_persistent_failure only monkeypatches Path.replace, so the fallback write_text still succeeds and the gap is untested. Monkeypatch Path.write_text to raise as well and assert write_status does not raise.

MINOR (not blocking, optional):
- is_stale (status.py:147-151) subtracts naive datetime.now() and only catches ValueError; a tz-aware timestamp would raise an uncaught TypeError and crash `runs`. All current timestamps are naive so no real risk today -- consider catching TypeError too for defensiveness.
- Scope note (no action required): suggestion #3's pid-based reconciliation (transition wedged runs to failed/interrupted) and #2 (non-locking readers) were intentionally not implemented. The chosen scope (root-cause #1 + lightweight stale detection) is reasonable; the rest can be follow-ups.

Re-submit after the BLOCKER fix + its test.

## Handoff

- Summary: Addressed review feedback. (1) write_status in-place fallback now wrapped in `except OSError: pass` so write_status never raises on lock contention (protects the main-thread terminal-status write in run()); temp file still cleaned in finally. Added test_write_status_does_not_raise_when_fallback_write_also_fails covering the both-paths-fail case. (2) is_stale now also catches TypeError for naive/aware datetime mismatch. Full suite 311 passed, 22 skipped; check-encoding clean.

**Completed**: 2026-06-17

## Completion Notes

- Approved by claude.
- Verification: `Approved. Both review findings addressed and verified.

BLOCKER (fixed): The in-place fallback in write_status (status.py:114-122) is now wrapped in `except OSError: pass`, so write_status genuinely never raises on lock contention -- protecting the main-thread terminal-status write at runner.py:507 from crashing run() before AgentResult is returned. Temp file still cleaned in `finally`. New test test_write_status_does_not_raise_when_fallback_write_also_fails reproduces the true contention case (both Path.replace and the destination Path.write_text raise PermissionError) and asserts no exception and no stray temp file -- covering the previously untested gap.

MINOR (fixed): is_stale now also catches TypeError, so a naive/aware datetime mismatch returns not-stale instead of crashing `runs`.

Verification:
- Full suite: 311 passed, 22 skipped (+2 new tests).
- issuekit check-encoding: clean (no BOM, mojibake, or CRLF); all changed files LF/no-BOM.

Core fix sound: _tick() guarded in runner.py:312-319 so a failed status write cannot kill the heartbeat thread; runs table marks stale running records (heartbeat older than STALE_AFTER_SEC) while JSON keeps raw "running". Pid-based reconciliation and non-locking readers remain optional follow-ups.`
