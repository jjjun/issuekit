# CLAUDE.md

See `AGENTS.md` for full agent guidelines.

Key points:

- Claude writes proposals, codex-ready issues, and reviews; codex implements the CLI.
- Implementation tasks live in `docs/issues/active/`. The issue tracker conventions are in `docs/issues/README.md`.
- This repo dogfoods its own issue tracker.
- Write all files as UTF-8 without a BOM and with LF line endings. Never introduce a UTF-8 BOM.
- `docs/issues/` content must be English ASCII only.
- Build/test with `uv sync` and `uv run pytest`.
