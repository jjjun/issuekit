---
origin: infra-toolkit#0@0c77cd2
to: issuekit
reply_to: 
created: 2026-06-17
title: Agent runs hang and show stale 'running' on Windows when the status-writer thread crashes (os.replace PermissionError)
---

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
