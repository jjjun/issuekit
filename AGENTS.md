# issuekit - Agent Guidelines

## Handoff protocol

This repo uses the issuekit multi-agent handoff. For the current steps, run
`issuekit protocol --role <role>` (e.g. `author`, `implementer`, `reviewer`,
`triage`, or `pm`), or read the issuekit MCP server instructions /
`get_protocol` tool. `issuekit protocol --agent <agent>` prints the configured
default role for that agent.

Do not copy the steps here; issuekit is the source of truth. Launch your agent
from the repo root so the MCP server resolves the repo configuration.

## Documentation

- Usage documentation lives in `docs/guides/`; start at `docs/README.md`.
- `docs/agent-notes/` is agent working memory. Read it before starting a task
  and write to it when you learn something operational that the guides and the
  code do not already record. See `docs/agent-notes/README.md`.

## Project notes

- Implementation tasks and cross-project proposals live in the configured API project.
- Authors write proposals and implementation-ready issues, then stop.
- Implementers claim active issues from the configured API project.
- Reviewers decide submitted issues and may also be the original author when a
  different implementer did the work.
- If the proposal-system MCP tools hang or error, use the equivalent CLI with
  `--json` (`issuekit propose/incoming/adopt`); they share one implementation.
- This repo dogfoods its own issue tracker.
- Repo-local `.env` is trusted input for `ISSUEKIT_*` keys only; sensitive API
  settings loaded from `.env` print a stderr notice.
- Write all files as UTF-8 without a BOM and with LF line endings.
- Build and test with `uv sync`, `uv run ruff check`, `uv run pytest`, and
  `uv run issuekit check-encoding`. Before submit, also run
  `uv run issuekit check-encoding --gate`.
- The dependency audit is automated. Code tests run manually only via
  `workflow_dispatch`; do not add `push` or `pull_request` triggers to
  `.github/workflows/tests.yml`.
