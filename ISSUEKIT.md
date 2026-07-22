# issuekit project profile

## Responsibilities

issuekit owns the shared, language-neutral multi-agent issue handoff workflow
used across repositories: authoring implementation-ready issues, the
author -> implement -> review delegation cycle, cross-project proposals, the
worker/role catalog, agent-driven implement/review/triage runs, and the
UTF-8/encoding guard. Issue lifecycle state and cross-project proposal state
live in a mine-py API project; issuekit is the client and workflow layer over
that API.

## Tech stack

- Python 3.12+, packaged with `uv` / hatchling.
- `httpx` HTTP client against the mine-py issuekit API (`IssuekitClient`).
- CLI dispatcher (`issuekit`) plus an optional FastMCP server (`issuekit-mcp`,
  installed with the `mcp` extra) exposing the same operations as MCP tools.
- Headless coding-agent adapters (codex, claude, kimi) driven by `AgentRunner`;
  see [`issuekit/agentrun/README.md`](issuekit/agentrun/README.md) for the
  runtime boundary and extension path.
- `pytest` for the test suite; a `check-encoding` gate enforces UTF-8 without
  BOM, no CRLF, and no mojibake in tracked files.

## Public surface

Every subpackage ``__init__.py`` has a module docstring and exposes its public
surface through an ``__all__`` facade when one is appropriate. Package initializers
do not contain implementation; implementation lives in focused submodules.

- CLI subcommands: author, claim, submit-review, review, approve,
  request-changes, complete, edit, queue, serve, implement, propose/incoming/
  outgoing/adopt/discard, negotiate/threads, triage, profile, workers, add,
  protocol, check-encoding, and more.
- MCP tools mirroring the workflow: get_protocol, claim_next_task,
  submit_for_review, next_review, approve, request_changes, propose,
  list_incoming/outgoing, adopt/discard_proposal, list_workers,
  list_project_profiles, get_issue, update_issue, list_queue.
- Library modules: `workflow`, `proposals_api`, `client`, `config`,
  `agents/*` (runner, review, triage_author), `project_profile`.

## Example in-scope requests

- "Add a `--priority` filter to the claim loop."
- "Make `serve --review` recover orphaned review-stage issues."
- "Enforce ASCII-only proposal bodies across the CLI and MCP entry points."
- "Add an agent-refined triage step that authors an implementation-ready spec
  before adopting a proposal."

## Example out-of-scope requests

- Server-side issue storage, API endpoints, or database schema changes (those
  belong to the mine-py project; send a cross-project proposal instead).
- Product features of the repositories that merely consume issuekit as a tool.
