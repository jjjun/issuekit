---
id: 72
status: completed
priority: low
created: 2026-06-29
completed: 2026-06-29
stage: done
author: claude
title: Load repo-local .env so ISSUEKIT_* settings persist across sessions
---

# Issue #72: Load repo-local .env so ISSUEKIT_* settings persist across sessions

## Problem

In API mode the operator must keep `ISSUEKIT_API_URL` (and optionally
`ISSUEKIT_API_USER` / `ISSUEKIT_API_PASSWORD` / `ISSUEKIT_PROJECT` / etc.) in the
process environment. That means re-exporting them every shell session. issuekit
does not read a `.env` file, so there is no persistent, repo-local place to set
these once.

`load_config` already reads several settings from the environment
(`ISSUEKIT_API_URL`, `ISSUEKIT_PROJECT`, `ISSUEKIT_API_TIMEOUT`,
`ISSUEKIT_USE_FILESYSTEM`), and the client reads `ISSUEKIT_API_USER` /
`ISSUEKIT_API_PASSWORD` / `ISSUEKIT_API_TOKEN` / `ISSUEKIT_TOKEN_CACHE` via
`os.getenv`. If a `.env` file is loaded into `os.environ` early, all of these
keep working unchanged.

## Goal

Automatically load a repo-local `.env` file so settings like
`ISSUEKIT_API_URL=http://192.168.10.192:28211` are picked up on every invocation
without manual `export`. Real process environment variables still win over
`.env` (so an explicit `export`/CI value overrides the file).

## Design

- Add a small built-in loader (no new dependency; deps are httpx only). A new
  helper, e.g. `issuekit/dotenv.py::load_dotenv(cwd)` or a private function in
  config.py:
  - Read `<cwd>/.env` if it exists. Missing file is a silent no-op.
  - Parse line by line: skip blank lines and lines whose first non-space char is
    `#`; ignore an optional leading `export ` prefix; split on the first `=`;
    strip surrounding whitespace from key and value; strip a single pair of
    matching surrounding quotes (`"` or `'`) from the value.
  - For each parsed key, use `os.environ.setdefault(key, value)` so a value
    already present in the real environment is NOT overwritten (process env >
    .env). Load all keys found, not only ISSUEKIT_* (standard .env behavior).
- Call the loader once at the start of `load_config` (the universal config
  chokepoint that every command and the MCP server funnel through), BEFORE the
  existing `os.getenv(...)` reads, so the values are visible to both
  `load_config` and any subsequently constructed `IssuekitClient`. Make it
  idempotent/cheap to call repeatedly.
- Resulting precedence: real process env > `.env` > config file
  (`[tool.issuekit]` / `issuekit.toml`) > hardcoded default. Document this.

## Security / notes

- `.env` is already in `.gitignore` (line 12), so credentials placed there are
  not committed. Do not change that. Do not log or print `.env` values.
- Do not error on a malformed line; skip lines without `=` rather than raising,
  to keep startup robust.

## Out of scope

- Walking up parent directories to find `.env` (load only `<cwd>/.env`). Can be
  a follow-up if needed.
- A `.env.example` template (optional; mention in README instead).

## Test Plan

- `uv run python -m pytest` (full suite) passes.
- New tests (use tmp_path as cwd and monkeypatch the environment):
  - A `.env` containing `ISSUEKIT_API_URL=...` makes `load_config(tmp)` return
    that `api_url` (and `use_filesystem_store` False).
  - A real `os.environ["ISSUEKIT_API_URL"]` overrides the `.env` value.
  - Comments, blank lines, surrounding quotes, and an `export ` prefix are
    handled; a malformed line without `=` is skipped without error.
  - Missing `.env` is a no-op and does not change behavior.
  - Ensure tests restore os.environ (monkeypatch) so they do not leak state.

## Related Resources

- issuekit/config.py (`load_config` env reads).
- issuekit/client.py (`os.getenv` credential/token reads).
- Follows #70 / #71 (login + token cache); completes the "set it once" UX.

## Handoff

- Summary: Implemented by codex via issuekit implement.

**Completed**: 2026-06-29

## Completion Notes

- Approved by claude.
- Verification: `Full suite green (334 passed, 25 skipped via uv run python -m pytest; +4 from prior, no test loss); issuekit check-encoding clean. Reviewed: new issuekit/dotenv.py::load_dotenv reads <cwd>/.env (FileNotFoundError -> silent no-op), parses line by line skipping blank/#/no-'=' lines, strips an optional 'export ' prefix (requires the trailing space, so keys like 'exporting=' are unaffected), splits on the first '=', strips a single pair of matching surrounding quotes, and applies os.environ.setdefault so real process env values are never overwritten. It is wired into load_config before the existing os.getenv reads, so both load_config and any subsequently constructed IssuekitClient see the values; precedence is process env > .env > [tool.issuekit]/issuekit.toml > defaults (documented in README). .env remains gitignored; no values are logged. Malformed lines are skipped rather than raising. Scope limited to issuekit/dotenv.py, config.py, README.md, and tests/test_config.py; no unrelated tracker files touched.`
