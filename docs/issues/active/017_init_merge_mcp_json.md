---
id: 17
status: active
priority: high
created: 2026-06-01
completed:
title: Merge issuekit into an existing .mcp.json instead of skipping
---


# Issue #17: Merge issuekit into an existing .mcp.json instead of skipping

## Problem

`issuekit init --with-mcp` writes `.mcp.json` through the generic
`_write_template` path, which skips the file entirely when it already exists
(unless `--force`). Real adopting repos already have a `.mcp.json` for their own
servers. Verified on 2026-06-01 in `py_cr_wrapper`: it already had a
`.mcp.json` with a `py-cr-wrapper` server, so `init --with-mcp` reported
"Skipped existing: .mcp.json" and the `issuekit` server was never added. Claude
Code therefore does not discover the issuekit MCP server in that repo, even
though the codex side worked (`.codex/config.toml` uses append-if-missing and
got the `[mcp_servers.issuekit]` block correctly).

`--force` is not an acceptable workaround because it would overwrite the repo's
existing server entries.

## Proposed Solution

Give `.mcp.json` the same idempotent append-if-missing behavior the codex config
already has, but as a structured JSON merge: parse the existing file, add the
`issuekit` server under `mcpServers` only when that key is absent, and write it
back. Preserve all other servers and the file's formatting as much as is
reasonable. When the file is absent, keep writing it from the template. When the
`issuekit` server already exists, skip (no duplicate, no overwrite).

## Impact

- Modified: `issuekit/commands/init.py` (replace the `_write_template` call for
  `.mcp.json` with a JSON-merge helper)
- New: `tests/test_init_mcp.py` cases for the merge paths
- Possibly modified: `issuekit/templates/mcp.json` stays the source of the
  issuekit entry shape

## Implementation Plan

1. Add `_write_mcp_json(cwd, force, result)` in `issuekit/commands/init.py`:
   - If `.mcp.json` is absent: write it from the `mcp.json` template (current
     behavior) and record it as written.
   - If present: parse it with `json.loads`. Read the issuekit server entry from
     the packaged `mcp.json` template (do not hardcode it inline; keep the
     template the single source of the entry shape). Under the top-level
     `mcpServers` object, add the `issuekit` key only if it is not already
     present. If `mcpServers` is missing, create it. Write back with
     `json.dumps(data, indent=2)` plus a trailing newline, UTF-8/LF/no BOM.
   - If the `issuekit` server key already exists: record skipped, change nothing.
   - On a parse error (malformed/non-object JSON), do not overwrite; record
     guidance telling the user to add the issuekit server manually, and include
     the template snippet.
2. Replace the `_write_template(cwd, cwd / ".mcp.json", "mcp.json", ...)` call in
   `_write_mcp_scaffold` with `_write_mcp_json(...)`.
3. Keep `--force` semantics: with `--force`, overwrite `.mcp.json` from the
   template as today (documented destructive behavior).
4. All writes go through UTF-8/LF/no-BOM; output ASCII only.

## Test Plan

- `uv run pytest tests/test_init_mcp.py`
- Absent file: `init_repo(tmp, with_mcp=True)` creates `.mcp.json` with the
  issuekit server (existing behavior preserved).
- Merge into existing: seed `.mcp.json` with a different server (for example
  `{"mcpServers": {"other": {"command": "x"}}}`); after `init --with-mcp`, both
  `other` and `issuekit` are present and `issuekit` uses `command:
  "issuekit-mcp"`.
- Idempotence: a second `init --with-mcp` does not duplicate the `issuekit`
  entry and does not modify `other`.
- No `mcpServers` key: seed `{}`; after init, `mcpServers.issuekit` exists.
- Malformed JSON: seed invalid JSON; init does not overwrite the file and emits
  guidance instead.
- All written files are ASCII-only with no BOM/CRLF; `json.loads` round-trips.
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/commands/init.py` (`_write_mcp_scaffold`, `_write_template`,
  `_write_codex_config` as the append-if-missing precedent)
- `issuekit/templates/mcp.json` (issuekit entry shape)
- Issue #16 (added `--with-mcp`; this fixes its `.mcp.json` skip limitation)
- Verified gap: `py_cr_wrapper/.mcp.json` (existing server, issuekit skipped)
