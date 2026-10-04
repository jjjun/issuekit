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
  installed with the `mcp` extra) exposing the tracker reads and state changes
  as MCP tools. Commands that launch other agents (`implement`, `review`,
  `serve`, `triage`, `request`, `negotiate`, `proposal-checks`) stay CLI-only; see
  [`docs/guides/mcp-server.md`](docs/guides/mcp-server.md#mcp-boundary).
- Headless coding-agent adapters (codex, claude, kimi) driven by `AgentRunner`
  through the default `exec` runtime. Codex implementer runs can opt into the
  `codex_app_server` runtime (`agentrun/app_server.py` transport,
  `agents/app_server_runtime.py` issue-owned attempts); see
  [`docs/guides/configuration.md`](docs/guides/configuration.md) and
  [`issuekit/agentrun/README.md`](issuekit/agentrun/README.md) for the
  runtime boundary and extension path.
- `pytest` for the test suite; a `check-encoding` gate enforces UTF-8 without
  BOM, no CRLF, and no mojibake in tracked files.

## Package layout

Packages are organized by responsibility. Keep new code with the responsibility
it serves rather than moving modules for symmetry.

- `agentrun`: reusable headless coding-agent process runtime; see
  [`issuekit/agentrun/README.md`](issuekit/agentrun/README.md) for its boundary
  and extension path. Tests: `test_agentrun_*.py` and `test_app_server.py`.
- `agents`: issuekit workflows that invoke agents, including implementation,
  review, routing, proposal checks, and triage. Tests: the corresponding
  `test_implement_command.py`, `test_review_command.py`, `test_router.py`,
  `test_proposal_checks.py`, `test_triage_author.py`, `test_readonly.py`, and
  `test_app_server_runtime.py` files.
- `api`: HTTP client, API resources, authentication, agent sessions, and token
  caching. Tests: `test_client.py` and `test_agent_sessions_client.py`.
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
  `test_worker.py`, `test_worker_keys.py`, `test_worker_addressing.py`, and
  `test_workers_command.py`.

`store.py` and `workflow.py` remain top-level because they are the workflow
core shared across the tracker-facing packages. `core.py` and `gitutil.py` are
their shared leaves, and `cli.py` is the thin top-level command dispatcher.
These modules are deliberately not nested: moving them only for symmetry would
blur the central workflow boundary. Reconsider their placement only when a
specific responsibility has a clear package boundary and its callers can depend
on that boundary instead of the shared workflow core.

The remaining top-level modules are small standard-library-only leaves shared
across packages: `file_permissions.py` (owner-only file and directory
permissions), `timestamps.py` (UTC timestamp parsing), and
`worker_constants.py` (worker heartbeat timing).

Dependencies point inward from entry points and workflows toward the API and
tracker layers. `commands`, `mcp`, `agents`, and `negotiation` sit above those
layers; `api`, `proposals`, `issues`, `guards`, `workers`, and `config` provide
focused support around them. `encoding`, `agentrun`, and `gitutil` are leaves
and must not import workflow state. In particular, nothing under
`issuekit/agentrun/` may import `issuekit.config`, `issuekit.workflow`,
`issuekit.store`, or `issuekit.proposals`.

## Public surface

Every subpackage ``__init__.py`` has a module docstring and exposes its public
surface through an ``__all__`` facade when one is appropriate. Most package
initializers are facades; `issuekit/commands/request/__init__.py` also implements
the PM request command.

- CLI subcommands: author, claim, submit-review, review, approve,
  request-changes, complete, edit, queue, serve, implement, propose/incoming/
  outgoing/adopt/discard, negotiate/threads, triage, profile, workers, add,
  protocol, check-encoding, and more.
- MCP tools for tracker reads and state changes: health, get_protocol,
  claim_next_task, submit_for_review, next_review, request_changes, approve,
  get_issue, update_issue, list_queue, list_workers, remove_worker,
  remove_repo, list_orphans, reclaim_issue, readdress_issue, dispatch_issue,
  list_project_profiles, propose, list_incoming/outgoing,
  list_negotiation_threads, adopt/discard_proposal, create_proposal_check,
  list_proposal_checks.
- Library modules: `workflow`, `proposals/client.py`, `proposals/build.py`,
  `proposals/send.py`, `proposals/adopt.py`, `proposals/outgoing.py`, `api/`,
  `config`, `agentrun/runner.py`, `agents/` (review, triage_author),
  `config/project_profile.py`.

## Example in-scope requests

- "Add a `--dry-run` option to `issuekit init` that previews scaffold files."
- "Add JSON output to `issuekit init` so scripts can inspect scaffold results."
- "Make `serve --review` recover orphaned review-stage issues."

## Example out-of-scope requests

- Server-side issue storage, API endpoints, or database schema changes (those
  belong to the mine-py project; send a cross-project proposal instead).
- Product features of the repositories that merely consume issuekit as a tool.
