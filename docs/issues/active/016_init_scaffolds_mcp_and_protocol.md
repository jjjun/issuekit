---
id: 16
status: in_progress
priority: high
created: 2026-06-01
completed:
assignee: codex
stage: implementing
title: Centralize handoff protocol and scaffold thin references from init
---

# Issue #16: Centralize handoff protocol and scaffold thin references from init

## Problem

Adopting the two-agent handoff workflow in another repo (for example
`py_cr_wrapper`) currently means hand-copying four things: `.mcp.json` for
Claude Code, an MCP block in `.codex/config.toml` for codex, and the full
handoff protocol text in `AGENTS.md` and `CLAUDE.md`. There are roughly six
adopting repos. If each repo embeds the full protocol text, every future change
to the operating rules has to be re-applied in all of them, which does not
scale.

The protocol must live in exactly one place (issuekit) so that improving it and
shipping a new issuekit version updates every repo at once. Each repo should
only carry a thin, stable reference that does not change when the protocol text
changes.

Confirmed codex behavior (verified 2026-06-01 with codex CLI 0.133.0, see issue
#13 follow-up): codex merges MCP server registrations from the global
`~/.codex/config.toml` and the project-local `.codex/config.toml`, resolved
against the working directory codex is launched from. The earlier registration
used `command = "uv", args = ["run", "--group", "mcp", "issuekit-mcp"]`, which
only resolves when codex is launched from this checkout (so `uv run` can find
the project) and silently fails to start the server everywhere else; codex then
reports that no issuekit MCP tools are available. The fix is to register the
global `issuekit-mcp` binary (PATH, cwd-independent) from issue #15, so the
server starts regardless of how or where the repo is opened. `issuekit init`
must scaffold that cwd-independent form, not the `uv run` form.

## Proposed Solution

Make issuekit the single source of truth for the handoff protocol text, exposed
as `issuekit protocol [--agent codex|claude]`. The MCP server reuses the same
text as its `instructions` and via a `get_protocol` tool, so an agent can fetch
the current protocol at runtime. `issuekit init --with-mcp` then scaffolds the
MCP registration files plus a thin reference block in `AGENTS.md` / `CLAUDE.md`
that points at `issuekit protocol` instead of embedding the steps. Updating the
protocol later is a single edit in issuekit and a `uv tool upgrade`; no repo
edits are required.

## Impact

- New: `issuekit/protocol.py` (the canonical protocol text, per agent role)
- New: `issuekit/commands/protocol.py` (the `protocol` subcommand)
- Modified: `issuekit/cli.py` (register `protocol`; add `--with-mcp` to `init`)
- Modified: `issuekit/mcp/server.py` (set `instructions=`; add `get_protocol`)
- Modified: `issuekit/commands/init.py` (write MCP files + thin reference blocks)
- New templates: `issuekit/templates/mcp.json`,
  `issuekit/templates/codex_config.toml`,
  `issuekit/templates/handoff_reference.md`
- Modified: `README.md`; `AGENTS.md` and `CLAUDE.md` in this repo become thin
  references too (dogfood the new model)
- New: `tests/test_protocol.py`, `tests/test_init_mcp.py`

## Implementation Plan

1. Add `issuekit/protocol.py` holding the canonical protocol text as ASCII
   string constants keyed by role (`"codex"`, `"claude"`), plus a helper
   `render_protocol(agent: str | None) -> str` (no agent -> both roles). Port the
   current wording from this repo's `AGENTS.md` / `CLAUDE.md` (claim -> plan ->
   implement -> submit-review; next_review -> approve/request_changes; stay on
   the current branch unless asked). This module is the single source of truth;
   nothing else hardcodes the steps.
2. Add `issuekit/commands/protocol.py` and register a `protocol` subcommand in
   `issuekit/cli.py`:
   - `issuekit protocol [--agent codex|claude]` prints `render_protocol(agent)`.
   - ASCII-only output, consistent with other commands.
3. In `issuekit/mcp/server.py`, pass `instructions=render_protocol(None)` to
   `FastMCP(...)`, and add a `get_protocol(agent=None)` tool that returns
   `render_protocol(agent)`. Both reuse `issuekit.protocol`; the server stores no
   protocol text of its own.
4. Add packaged templates under `issuekit/templates/` that invoke the global
   `issuekit-mcp` binary from issue #15 (no `uv run`):
   - `mcp.json`: `{ "mcpServers": { "issuekit": { "command": "issuekit-mcp",
     "args": [] } } }`. Do not hardcode an absolute `cwd`; the server resolves
     the target repo from its working directory.
   - `codex_config.toml`: the `[mcp_servers.issuekit]` block with
     `command = "issuekit-mcp"`, `args = []`. Do NOT emit the `uv run --group
     mcp issuekit-mcp` form: it only works when codex launches from the
     issuekit checkout and fails elsewhere (the bug this issue fixes). The
     server resolves the target repo from its working directory, so codex must
     be launched from the consuming repo's root; document that in the reference
     block (step 4 `handoff_reference.md`).
   - `handoff_reference.md`: a short, protocol-version-independent block, for
     example: "## Handoff protocol\n\nThis repo uses the issuekit two-agent
     handoff. For the current steps run `issuekit protocol --agent codex`
     (codex) or `issuekit protocol --agent claude` (claude), or read the
     issuekit MCP server instructions / `get_protocol` tool. Do not copy the
     steps here; issuekit is the source of truth." Also state that codex/Claude
     Code must be launched from the repo root so the MCP server resolves the
     correct `docs/issues/`. The reference text must not restate the steps, so
     it never needs updating when the protocol changes.
5. Add `--with-mcp` to the `init` subparser (default off; current behavior
   unchanged). When set, `init_repo` writes, using the existing idempotent
   no-overwrite-unless-force logic:
   - `.mcp.json` via `_write_template`.
   - `.codex/config.toml`: if absent, write from template; if present, append the
     `[mcp_servers.issuekit]` block only when that key is missing (mirror the
     `_write_pre_commit` append-if-missing pattern; never duplicate). Create
     `.codex/` as needed.
   - `AGENTS.md` / `CLAUDE.md`: if absent, write a minimal file containing the
     thin reference; if present, append the reference block only when the marker
     heading `## Handoff protocol` is absent. Never rewrite unrelated content.
6. All writes are UTF-8 without BOM and LF (reuse `_write_template`'s
   `newline="\n"`); all template and protocol text is English ASCII only.
7. Update `README.md`: one-time `uv tool install "issuekit[mcp] @ ..."` (issue
   #15) plus per-repo `issuekit init --with-mcp`, and document `issuekit
   protocol`. Replace the embedded protocol sections in this repo's `AGENTS.md`
   and `CLAUDE.md` with the thin reference so issuekit dogfoods the new model.

## Test Plan

- `uv run pytest tests/test_protocol.py tests/test_init_mcp.py`
- `issuekit protocol --agent codex` prints the codex steps; `--agent claude`
  prints the claude steps; no `--agent` prints both. Output is ASCII-only.
- Single source: assert the MCP `get_protocol` output and the CLI output come
  from the same `render_protocol` (identical text), so they cannot drift.
- `init_repo(tmp, with_mcp=True)` writes `.mcp.json` (referencing the
  `issuekit-mcp` command, not `uv run`), `.codex/config.toml`, and a thin
  reference block in `AGENTS.md` / `CLAUDE.md` that contains `issuekit protocol`
  and does NOT contain the literal step list.
- Idempotence: a second `init --with-mcp` does not duplicate the MCP block or the
  reference block and does not overwrite without `--force`.
- Append path: pre-existing `.codex/config.toml` and `AGENTS.md` lacking the
  issuekit entries get the block/reference appended once; unrelated content is
  preserved.
- Default off: `init_repo(tmp)` writes none of the MCP files.
- All written files are ASCII-only with no BOM/CRLF.
- The scaffolded `.codex/config.toml` and `.mcp.json` use `command =
  "issuekit-mcp"` and never the `uv run` form (assert by string match).
- Manual end-to-end (the check that issue #13 skipped and let the `uv run`
  regression through): with `issuekit[mcp]` installed globally and a repo
  scaffolded via `init --with-mcp`, start the issuekit MCP server and drive an
  `initialize` + `tools/list` handshake (line-delimited JSON over stdio); assert
  the seven tools appear: `claim_next_task`, `submit_for_review`, `next_review`,
  `request_changes`, `approve`, `get_issue`, `list_queue`. Then confirm `codex
  mcp get issuekit` resolves to `command: issuekit-mcp`.
- Run full `uv run pytest` and `uv run issuekit validate`.

## Related Resources

- `issuekit/commands/init.py` (`init_repo`, `_write_template`, `_write_pre_commit`)
- `issuekit/cli.py` (subcommand registration, `init` subparser)
- `issuekit/mcp/server.py` (`create_server`, `FastMCP` `instructions`)
- `issuekit/templates/` (existing packaged templates)
- This repo's `AGENTS.md` / `CLAUDE.md` (source wording for `protocol.py`)
- Issue #15 (required; provides the global `issuekit-mcp` binary)
