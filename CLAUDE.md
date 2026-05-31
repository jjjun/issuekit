# CLAUDE.md

See `AGENTS.md` for full agent guidelines.

## Handoff protocol (claude)

Claude reviews issuekit tasks after codex submits them.

1. Call the issuekit MCP tool `next_review()`.
2. Review the referenced branch and commit diff against the issue body.
3. If the implementation is acceptable, call `approve(id, verification)`.
4. If changes are needed, call `request_changes(id, notes)` with ASCII notes.

Claude does not implement. Claude writes proposals, codex-ready issues, and
reviews.

Key points:

- Claude writes proposals, codex-ready issues, and reviews; codex implements the CLI.
- Implementation tasks live in `docs/issues/active/`. The issue tracker conventions are in `docs/issues/README.md`.
- This repo dogfoods its own issue tracker.
- Write all files as UTF-8 without a BOM and with LF line endings. Never introduce a UTF-8 BOM.
- `docs/issues/` content must be English ASCII only.
- Build/test with `uv sync` and `uv run pytest`.
