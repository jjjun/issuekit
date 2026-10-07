# Agent notes index

One line per note. Add an entry when you add a note; remove it when you delete
one. See [README.md](README.md) for the rules.

- [Waiting on issuekit implement runs](waiting-on-implement-runs.md) - wait for
  `show --json` to report a dead local run, then rerun `issuekit implement`; the
  orphan commands inspect API worker heartbeats only.
- [ASCII-only review fields](ascii-only-review-fields.md) - `approve` and
  `submit-review` text fields reject non-ASCII characters.
- [CI policy](ci-policy.md) - which workflows are automatic and which are
  manual-only, and why.
- [One agent maps to one default protocol role](agent-roles-single-default.md) -
  built-in defaults are codex=implementer and claude=reviewer; use
  `issuekit protocol --role <role>` when the role is known.
- [MCP test coverage](mcp-test-coverage.md) - without the `mcp` dependency the
  whole MCP test file skips silently and the suite still reports green; keeping
  it in the dev group is deliberate.
- [Adoption notes never reach the proposal sender](adoption-notes-do-not-reach-the-sender.md) -
  `adopt --append-file` writes to the receiving issue; use `propose --reply`
  to send a linked response.
- [Proposal origins are per source issue and commit](proposal-origin-dedup.md) -
  origins are per source issue and commit; mismatches compare all proposal
  payload fields and fail with `payload_mismatch`.
- [Checking the mine-py API contract](mine-py-openapi-check.md) - the deployed
  `/openapi.json` is 404; generate it offline with `mine-export-openapi`, and
  probe the live server read-only when deployment state matters.
- [Verification-only issues cannot go through implement/submit](verification-only-issues.md) -
  no-diff runs need `--allow-no-changes` for review; no-ops use `complete
  --force`, while `orphans` tracks worker heartbeats.
- [Encoding check modes](encoding-check-modes.md) - use `check-encoding --gate`
  to reproduce submit behavior; the default command intentionally has broader
  whole-file and line-ending checks.
- [Follow heartbeat git status](follow-heartbeat-git-status.md) - the exec run
  heartbeat is lock-free and safe while an issue rewrites the checkout.
- [Adopt append failures](adopt-append-failures.md) - a failed append exits 1
  and leaves a claimable issue without its scope; the mine-py API commits after
  responding, so an immediate read can miss a write.
- [Checking agent model ids and effort levels](checking-agent-model-ids.md) -
  model ids pass through unchecked; bare `gpt-5.6` fails under ChatGPT sign-in.
  Where to find valid slugs and efforts, and how to smoke-test them.
- [Read-only agent sandbox policies](read-only-agent-sandbox.md) - verified
  Codex and Claude CLI launch flags, MCP disabling, and the Claude sandbox
  dependency limitation in this environment.
- [Checking issuekit-mcp from real clients](real-client-mcp-checks.md) - probe
  flags for Claude Code and Codex; Codex needs tool auto-approval and forwarded
  `ISSUEKIT_*` env vars.
- [Run issuekit implement in its own shell command](implement-own-shell.md) -
  do not chain it after `git commit`; the implementer can see and kill its
  parent shell. Resume an interrupted run with `--no-sync`.
