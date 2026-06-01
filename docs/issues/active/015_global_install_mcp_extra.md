---
id: 15
status: active
priority: high
created: 2026-06-01
title: Make the issuekit MCP server installable via global uv tool install
---


# Issue #15: Make the issuekit MCP server installable via global uv tool install

## Problem

The handoff workflow should be usable from any repo that adopts issuekit, not
only this repo. The chosen distribution model is a single global install
(`uv tool install`) so every project just registers the server and writes no
wiring of its own. But the `mcp` requirement currently lives in
`[dependency-groups]` (PEP 735), which `uv tool install` does not install. As a
result a globally installed `issuekit-mcp` would fail to import `mcp` at
startup. The MCP server is also reachable today only via `uv run --group mcp`,
which assumes a local checkout rather than a global tool.

## Proposed Solution

Expose `mcp` as an installable optional dependency (an extra) so
`uv tool install "issuekit[mcp]"` provides a working `issuekit-mcp` on PATH. The
server already resolves the target repo from `Path.cwd()` (see
`issuekit/mcp/server.py` `create_server`), so a single global binary works for
every project without per-repo wiring. Keep the default install dependency-free;
`mcp` stays opt-in. Keep the existing `[dependency-groups] mcp` entry so local
`uv run --group mcp` and the test suite keep working.

## Impact

- Modified: `pyproject.toml` (add `[project.optional-dependencies] mcp`)
- Modified: `README.md` (global install instructions for the MCP server)
- New: `tests/test_packaging.py` (assert the `mcp` extra exists and lists `mcp`)

## Implementation Plan

1. In `pyproject.toml` add an extra mirroring the existing group:
   ```toml
   [project.optional-dependencies]
   mcp = ["mcp>=1.0"]
   ```
   Leave `[dependency-groups] mcp = ["mcp>=1.0"]` in place so local development
   (`uv run --group mcp ...`) and CI are unchanged. Keep `[project] dependencies`
   empty so the core CLI stays dependency-free.
2. Confirm `issuekit-mcp` remains declared under `[project.scripts]` so the
   global install puts it on PATH.
3. In `README.md`, document the global install path as the primary way to use the
   MCP server across repos:
   ```powershell
   uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
   ```
   Keep the local `uv run --group mcp issuekit-mcp` note for development.
4. Add `tests/test_packaging.py` that loads `pyproject.toml` with `tomllib` and
   asserts `project.optional-dependencies.mcp` exists and contains an `mcp`
   requirement, so the extra cannot silently regress.

## Test Plan

- `uv run pytest tests/test_packaging.py`
- Parse `pyproject.toml`; assert the `mcp` extra is present and lists `mcp>=...`.
- Confirm `import issuekit.cli` still works with no extra installed (core stays
  dependency-free).
- Manual (optional, no network in CI): `uv tool install "issuekit[mcp] @
  <path-or-git>"` then `issuekit-mcp` starts and serves stdio from an arbitrary
  repo's working directory.
- Run full `uv run pytest`.

## Related Resources

- `pyproject.toml` (`[dependency-groups] mcp`, `[project.scripts]`)
- `issuekit/mcp/server.py` (`create_server`, cwd-based repo resolution)
- `README.md`
- Issue #16 (uses the global binary; `issuekit init` scaffolds registration)
