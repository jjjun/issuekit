---
id: 71
status: active
priority: low
created: 2026-06-29
completed: 
stage: todo
author: claude
title: issuekit login: prompt for username interactively on a TTY
---

# Issue #71: issuekit login: prompt for username interactively on a TTY

## Problem

`issuekit login` (issuekit/commands/auth.py, added in #70) only prompts
interactively for the PASSWORD (via getpass when stdin is a TTY). The USERNAME
must still be supplied non-interactively through `--user` or the
`ISSUEKIT_API_USER` env var; when neither is set the command errors out instead
of asking. For a hands-on `issuekit login`, the operator should be able to type
the username interactively too.

## Goal

Make `issuekit login` fully interactive on a TTY: when the username is not
provided via `--user` or `ISSUEKIT_API_USER`, prompt for it; the password prompt
(getpass) already works and should be unchanged.

## Design

In `run_login` (issuekit/commands/auth.py):

- Resolve username as today: `args.user or os.getenv("ISSUEKIT_API_USER")`.
- If still empty AND `sys.stdin.isatty()`, prompt for it with
  `input("Issuekit API username: ")` and strip the result.
- If still empty after that (non-TTY, or the user entered nothing), keep the
  current clear error: "API username is required; pass --user or set
  ISSUEKIT_API_USER."
- Keep the existing password handling: env `ISSUEKIT_API_PASSWORD`, else
  `getpass` when a TTY is available, else the current error.
- Order the prompts naturally: username first, then password.

Non-interactive behavior (CI, no TTY) must be unchanged: no prompts, same error
messages when values are missing. Do not echo the password. Do not print the
token.

## Out of scope

- Prompting for `api_url` (that is configuration, not a per-login secret).
- Any change to `logout`, the token cache, or the client.

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- New tests in tests/test_cli.py (or tests/test_client.py alongside the existing
  login tests):
  - With no `--user` and no `ISSUEKIT_API_USER`, a simulated TTY + mocked
    `input` supplies the username and login proceeds (mock the password
    source too); the entered username is used.
  - Non-TTY with no username still errors with the existing message and does
    NOT call `input`.
  - `--user` / `ISSUEKIT_API_USER` still bypass the prompt.

## Related Resources

- issuekit/commands/auth.py (`run_login`).
- Follows #70 (token cache + login/logout commands).
