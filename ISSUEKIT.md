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

## Package layout

Packages are organized by responsibility. Keep new code with the responsibility
it serves rather than moving modules for symmetry.

- `agentrun`: reusable headless coding-agent process runtime; see
  [`issuekit/agentrun/README.md`](issuekit/agentrun/README.md) for its boundary
  and extension path. Tests: `test_agentrun_*.py`.
- `agents`: issuekit workflows that invoke agents, including implementation,
  review, routing, proposal checks, and triage. Tests: the corresponding
  `test_implement_command.py`, `test_review_command.py`, `test_router.py`,
  `test_proposal_checks.py`, and `test_triage_author.py` files.
- `api`: HTTP client, API resources, authentication, and token caching. Tests:
  `test_client.py`.
- `commands`: CLI subcommand implementations and setup helpers. Tests: the
  command-named `test_*_command.py` files, plus `test_setup.py` and
  `test_validate.py`.
- `config`: TOML, environment, local-worker, reference, and project-profile
  configuration. Tests: `test_config.py`, `test_localconfig.py`, `test_refs.py`,
  and `test_project_profile.py`.
- `encoding`: encoding and mojibake detection and reporting. Tests:
  `test_encoding.py` and `test_check_encoding.py`.
- `guards`: author-handoff, branch, claim-sync, and separation-of-duties
  protections. Tests: `test_branch_guard.py`, `test_claim_sync.py`, and the
  related command tests.
- `issues`: issue dependency, display, stale-claim, and session helpers. Tests:
  `test_orphans.py`, `test_session.py`, and the related lifecycle tests.
- `mcp`: optional MCP server integration. Tests: `test_mcp_server.py` and
  `test_init_mcp.py`.
- `negotiation`: negotiation thread model, storage backends, engine, and
  prompts. Tests: `test_negotiation.py`, `test_negotiation17_contract.py`, and
  `test_negotiation_prompts.py`.
- `proposals`: cross-repository proposal model and API helpers. Tests:
  `test_proposals.py`.
- `prompts`: agent prompt templates and structured-output contracts. Tests:
  `test_prompts.py` and the workflow-specific prompt tests.
- `templates`: packaged files used by project initialization. Tests:
  `test_init.py` and `test_setup.py`.
- `testing`: reusable in-memory test doubles for issue and proposal APIs. Tests
  use these helpers throughout `tests/`; no separate test module owns them.
- `workers`: worker identity, registration, and API registry helpers. Tests:
  `test_worker.py`, `test_worker_keys.py`, and `test_workers_command.py`.

`store.py` and `workflow.py` remain top-level because they are the workflow
core shared across the tracker-facing packages. `core.py` and `gitutil.py` are
their shared leaves, and `cli.py` is the thin top-level command dispatcher.
These modules are deliberately not nested: moving them only for symmetry would
blur the central workflow boundary. Reconsider their placement only when a
specific responsibility has a clear package boundary and its callers can depend
on that boundary instead of the shared workflow core.

Dependencies point inward from entry points and workflows toward the API and
tracker layers. `commands`, `mcp`, `agents`, and `negotiation` sit above those
layers; `api`, `proposals`, `issues`, `guards`, `workers`, and `config` provide
focused support around them. `encoding`, `agentrun`, and `gitutil` are leaves
and must not import workflow state. In particular, nothing under
`issuekit/agentrun/` may import `issuekit.config`, `issuekit.workflow`,
`issuekit.store`, or `issuekit.proposals`.

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
- Library modules: `workflow`, `proposals/api.py`, `api/`, `config`,
  `agentrun/runner.py`, `agents/` (review, triage_author),
  `config/project_profile.py`.

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
