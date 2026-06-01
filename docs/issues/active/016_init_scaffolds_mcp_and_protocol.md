---
id: 16
status: active
priority: high
created: 2026-06-01
title: Scaffold MCP registration and handoff protocol from issuekit init
---


# Issue #16: Scaffold MCP registration and handoff protocol from issuekit init

## Problem

Adopting the two-agent handoff workflow in another repo (for example
`py_cr_wrapper`) currently means hand-copying four things: `.mcp.json` for
Claude Code, an MCP block in `.codex/config.toml` for codex, and the handoff
protocol sections in `AGENTS.md` and `CLAUDE.md`. `issuekit init` already
scaffolds the tracker (`docs/issues`, `.gitattributes`, `.editorconfig`,
pre-commit) but not the MCP registration or the protocol docs, so the workflow
is not "init and go" in a new repo.

## Proposed Solution

Extend `issuekit init` to also scaffold the MCP registration files and the
handoff protocol documentation, behind an opt-in flag so existing init behavior
is unchanged by default. Ship these as packaged templates (same mechanism as the
current `gitattributes` / `issues_README.md` templates) and write them with the
existing idempotent, no-overwrite-unless-force logic. The registration assumes
the globally installed `issuekit-mcp` from issue #15, so the templates invoke the
bare `issuekit-mcp` command (no `uv run`).

## Impact

- Modified: `issuekit/commands/init.py` (write MCP + protocol templates)
- Modified: `issuekit/cli.py` (add `--with-mcp` flag to the `init` subparser)
- New templates: `issuekit/templates/mcp.json`,
  `issuekit/templates/codex_config.toml`,
  `issuekit/templates/handoff_codex.md`, `issuekit/templates/handoff_claude.md`
- Modified: `README.md` (document `issuekit init --with-mcp`)
- New: `tests/test_init_mcp.py`

## Implementation Plan

1. Add `--with-mcp` to the `init` subparser in `issuekit/cli.py` (default off so
   current behavior is preserved). Pass it through to `init_repo`.
2. Add packaged templates under `issuekit/templates/`:
   - `mcp.json`: a Claude Code server entry that runs the global binary:
     ```json
     {
       "mcpServers": {
         "issuekit": { "command": "issuekit-mcp", "args": [] }
       }
     }
     ```
     Do not hardcode an absolute `cwd`; the server resolves the target repo from
     its working directory, which Claude Code sets to the project root.
   - `codex_config.toml`: the `[mcp_servers.issuekit]` block with
     `command = "issuekit-mcp"` and `args = []`.
   - `handoff_codex.md` / `handoff_claude.md`: the codex and claude protocol
     sections, derived from the current `AGENTS.md` / `CLAUDE.md` text in this
     repo (claim -> plan -> implement -> submit-review; next_review ->
     approve/request_changes; stay on the current branch unless asked).
3. In `init_repo`, when `with_mcp` is set:
   - Write `.mcp.json` via the existing `_write_template` path (no overwrite
     unless `--force`).
   - For `.codex/config.toml`: if absent, write it from the template; if present,
     append the `[mcp_servers.issuekit]` block only when that key is not already
     there (mirror the `_write_pre_commit` "append guidance if missing" pattern;
     never duplicate a block). Create the `.codex/` directory as needed.
   - For `AGENTS.md` / `CLAUDE.md`: if absent, write a minimal file containing the
     protocol section; if present, append the protocol section only when a marker
     heading (for example `## Handoff protocol`) is not already present. Do not
     rewrite unrelated content.
4. All writes are UTF-8 without BOM and LF (reuse `_write_template`'s
   `newline="\n"`), and all template text is English ASCII only.
5. Update `README.md` to show the one-time global install (issue #15) plus
   `issuekit init --with-mcp` as the per-repo setup.

## Test Plan

- `uv run pytest tests/test_init_mcp.py`
- `init_repo(tmp, with_mcp=True)` on an empty repo writes `.mcp.json`,
  `.codex/config.toml`, and protocol sections into `AGENTS.md` / `CLAUDE.md`,
  and the `.mcp.json` references the `issuekit-mcp` command (not `uv run`).
- Idempotence: a second `init --with-mcp` run does not duplicate the
  `[mcp_servers.issuekit]` block or the protocol sections, and does not
  overwrite existing files without `--force`.
- Append path: with a pre-existing `.codex/config.toml` and `AGENTS.md` that lack
  the issuekit entries, the blocks/sections are appended once and unrelated
  content is preserved.
- Default off: `init_repo(tmp)` without the flag writes none of the MCP files
  (existing behavior unchanged).
- All written files contain only ASCII and have no BOM/CRLF.
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/commands/init.py` (`init_repo`, `_write_template`, `_write_pre_commit`)
- `issuekit/cli.py` (`init` subparser)
- `issuekit/templates/` (existing packaged templates)
- This repo's `.mcp.json`, `.codex/config.toml`, `AGENTS.md`, `CLAUDE.md`
  (source text for the templates)
- Issue #15 (required; provides the global `issuekit-mcp` binary)
