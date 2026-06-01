---
id: 18
status: active
priority: medium
created: 2026-06-01
completed:
title: Add issuekit setup command with diagnostics and codex mcp add guidance
---


# Issue #18: Add issuekit setup command with diagnostics and codex mcp add guidance

## Problem

Onboarding a repo to the two-agent handoff still has manual, error-prone parts
that bit us during rollout:

- The global tool must be installed with the `mcp` extra (`uv tool install
  "issuekit[mcp] @ ..."`). If it is installed without the extra, `issuekit-mcp`
  is missing and the MCP server never starts. We hit exactly this.
- Updating the global tool while an MCP server is running fails: the running
  `issuekit-mcp.exe` holds the executable and `uv tool install --force` left the
  install half-removed (`No module named 'issuekit'`). We hit this too.
- Installing from a relative path (`@ .`) produced an inconsistent install that
  resolved to different code depending on the working directory; a stale
  same-version wheel in `dist/` was reused from cache. Absolute path plus
  `--reinstall` fixed it.
- After `init --with-mcp`, codex usually discovers the server from
  `.codex/config.toml`, but if a user relies on the global `~/.codex/config.toml`
  or the `codex mcp` store, they need `codex mcp add issuekit -- issuekit-mcp`.
  There is no guidance telling them when that is needed.
- Reinstalling or upgrading the global tool invalidates the MCP connections of
  agent sessions that are already running. An agent (codex or Claude Code)
  spawns the `issuekit-mcp` server once at session start and holds that stdio
  connection; if the binary is killed or replaced underneath it, the next tool
  call fails with a closed-transport error (observed: "MCP transport closed,
  claim failed; falling back to issuekit claim --assignee codex"). The CLI keeps
  working because each CLI invocation is a fresh short-lived process. There is no
  note telling users to restart their agent sessions after a global reinstall.

There is no single command that checks these preconditions and reports what is
wrong, so each new repo rediscovers the same traps.

## Proposed Solution

Add `issuekit setup` that runs `init --with-mcp` and then a diagnostics pass,
printing a clear, ASCII checklist of what is correct and what the user must do
by hand (the parts issuekit cannot safely automate, like upgrading a
locked global binary or editing the user's global codex config). Also emit a
ready-to-paste `codex mcp add` command as guidance (do not execute it). The
command never kills processes and never edits files outside the repo.

## Impact

- New: `issuekit/commands/setup.py`
- Modified: `issuekit/cli.py` (register `setup`)
- Modified: `README.md` (document `setup` as the per-repo entry point and the
  one-time global install/upgrade caveats)
- New: `tests/test_setup.py`

## Implementation Plan

1. Add `issuekit/commands/setup.py` with `run(args)` that:
   - Calls `init_repo(cwd, force=args.force, with_mcp=True)` (reuses issue #16 and
     the issue #17 `.mcp.json` merge) and prints its written/skipped/guidance
     output.
   - Runs read-only diagnostics and prints a checklist:
     - Is `issuekit-mcp` importable / on PATH? (detect the missing-`mcp`-extra
       case; if missing, advise `uv tool install "issuekit[mcp] @ <url>"`).
     - Does `.mcp.json` contain an `issuekit` server? Does `.codex/config.toml`
       contain `[mcp_servers.issuekit]`? Do `AGENTS.md` / `CLAUDE.md` contain the
       handoff reference?
     - Print the exact `codex mcp add issuekit -- issuekit-mcp` command as
       optional guidance for users who manage codex via the global store, and
       note it is unnecessary if codex reads the project `.codex/config.toml`.
   - Prints, as plain guidance (not executed), the safe global-update note: to
     upgrade the global tool, stop running issuekit MCP servers first (MCP
     clients hold `issuekit-mcp.exe`), then
     `uv tool install --reinstall "issuekit[mcp] @ <absolute-path-or-url>"`.
     Recommend an absolute path, never a bare `.`, to avoid the cwd-dependent
     install we observed. State that after any reinstall or upgrade, running
     agent sessions (codex, Claude Code) must be restarted, because they hold a
     stdio connection to the old server process and will otherwise hit a
     closed-transport error on the next tool call.
   - Returns 0 when the repo-side scaffold is in place even if optional global
     steps remain; returns non-zero only on a real failure (for example cannot
     write the repo files).
2. `setup` performs no process management and no edits outside the repo; all
   destructive or global actions are guidance text only.
3. Register `setup` in `issuekit/cli.py` with `--force` forwarded to `init_repo`.
4. Update `README.md`: `issuekit setup` is the per-repo command; document the
   one-time global install with the `mcp` extra and the locked-binary upgrade
   caveat.

## Test Plan

- `uv run pytest tests/test_setup.py`
- `setup` on an empty repo scaffolds the same files as `init --with-mcp` and
  prints a checklist; exit code 0.
- Diagnostics detect and report a missing `issuekit` entry in `.mcp.json`
  (before issue #17 merge runs) versus present (after), and a present/absent
  `[mcp_servers.issuekit]` in `.codex/config.toml`.
- The printed `codex mcp add issuekit -- issuekit-mcp` guidance appears verbatim
  and is not executed (no subprocess spawned; assert via monkeypatch that no
  process runs).
- Output is ASCII-only; no files outside the repo are touched (assert by
  checking only repo-relative writes occur).
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/commands/init.py` (`init_repo`)
- `issuekit/cli.py` (subcommand registration)
- `README.md`
- Issue #16 (`--with-mcp` scaffold), Issue #17 (`.mcp.json` merge; required so
  `setup` reports `.mcp.json` as correct)
