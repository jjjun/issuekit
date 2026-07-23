"""Canonical agent handoff protocol text."""

from __future__ import annotations

from collections.abc import Iterable

from issuekit.guards.separation import SEPARATION_GUARD_REFERENCE


CYCLE_PROTOCOL = f"""# Delegation cycle overview

The canonical delegation cycle is:

1. Author: write an implementation-ready issue or proposal, then stop.
2. Open implement pool: leave `assignee` empty unless a specific implementer is
   required, so any idle configured agent can claim the issue.
3. Implementer: claim the issue with `claim_next_task`, run the work, then call
   `submit_for_review`.
4. Open review pool: omit `reviewer` when `default_reviewer = "auto"` so any
   eligible reviewer can decide the issue.
5. Reviewer: approve to complete the issue, or request changes to return it to
   implementation.
6. Changes loop: the implementer reclaims or continues the issue, addresses only
   the review feedback, and submits for review again.

The model is pull-based: authors publish work to a pool, implementers pull from
that pool, and reviewers pull from the review pool. No central orchestrator is
required for the normal author -> implement -> review cycle.

Register each checkout once with `issuekit add` (alias `issuekit register`)
before pulling work. It records repo_id, worker_name, and machine metadata in a
gitignored `issuekit.local.toml`, so claims report which worker checkout holds
an issue as `worker.repo`. Multiple checkouts of one repo can use distinct
worker names.

Issue lifecycle and cross-project proposal state are stored in the configured
mine-py API project.

If MCP tools return `Transport closed`, the client stdio transport is dead even
if tool metadata is still visible. Fall back to read-only or proposal/inbox CLI
commands such as `issuekit protocol --role author`, `issuekit incoming --json`,
and `issuekit info --json` until the user reloads or restarts the MCP client
session.

When an orchestrator or author needs to drive a configured external
implementer instead of waiting for the pull model, use
`issuekit implement <id> --agent <agent> --timeout-sec <n>`. That command
claims or operates on the assigned issue, launches the configured agent, and
submits the completed work for review. This is a sanctioned orchestration path:
the parent session launches a distinct implementer run session and records the
orchestrator in the submit summary. It is different from
`--allow-author-session`, which is only a human emergency bypass for a local
STOP guard. Prefer a clean worktree before orchestrating so existing author
edits are not attributed to the implementer run.

By default, the built-in Codex config runs without a sandbox and relies on the
repository worktree plus the review gate. Projects that require the strict
sandbox can set `[agents.codex] approval_flag = "--full-auto"`, or set
`approval_flag = "--sandbox"` with `approval_value = "workspace-write"`.
The built-in Claude config uses `--permission-mode bypassPermissions` for
unattended headless runs; stricter projects can set
`[agents.claude] approval_value = "acceptEdits"`.

Agent-launching commands accept pass-through `--model <model-id>` and
`--reasoning-effort <value>` overrides.
This includes `implement`, `review`, `negotiate`, `serve`, `triage`, and
`proposal-checks`; issuekit does not maintain a model allowlist. Set an agent's
defaults with `[agents.codex] model = "gpt-5.6"` and
`[agents.codex] reasoning_effort = "medium"`. The optional
`[agents.codex.model_prompts]` entries append model-specific guidance and their
keys must match the exact model id string. Per-run values override configured
defaults. A reasoning effort setting requires an `effort_argv` template; the
built-in Codex adapter uses `("-c", "model_reasoning_effort={{value}}")` and the
built-in Claude adapter uses `("--effort", "{{value}}")`.
Serve-level overrides apply to every agent launched; use per-agent overlays
for mixed-agent setups, or `[agents.<name>.roles.<role>]` model and
reasoning-effort overlays when one agent serves multiple roles.

Commands and MCP tools that omit an implementer resolve it as an explicit value,
then `default_implementer`, then the single enabled assignee. They fail with a
clear message when more than one enabled assignee exists and no default is set.
Use `[agent_roles]` to select the protocol text an agent receives from
`issuekit protocol --agent <agent>` and `get_protocol(agent=...)`; `--role` or
`role=` always takes precedence over the agent default.

When a reviewer daemon is needed, run it from a separate registered checkout:
`issuekit serve --agent <reviewer> --review`. For a one-shot agent review of a
specific review-stage issue, use `issuekit review <id> --agent <reviewer>`.

Upstream feedback loop: the issuekit tool itself accepts proposals. Whenever
work in any project surfaces an issuekit bug, limitation, or improvement idea
(CLI, MCP tools, protocol text, agent adapters), report it before finishing
the task with `issuekit propose --to issuekit --title <t> --body <b>` (or the
MCP `propose` tool). Include reproduction steps or the concrete gap, and pass
`--from-issue <id>` when the report stems from a specific local issue so each
report gets a distinct origin. The issuekit project triages its inbox
continuously and adopts worthwhile reports as issues; check the outcome later
with `issuekit outgoing --to issuekit`.

Local issues vs. cross-project proposals:

- Use `issuekit author` only for work that originates in and belongs to the
  current project.
- If you are acting from project A and the change belongs to project B, stay in
  project A and run `issuekit propose --to B --title <t> --body <b>` instead of
  changing directories into B and running `issuekit author`.
- When a requested change spans multiple projects, identify the project that
  owns the first required contract or API change. Create or propose that
  upstream owner work first, then send downstream consumer proposals only after
  the upstream proposal or issue exists. Reference it with
  `--depends-on <project#N|project#issue:N|project#proposal:N>` on local issues
  or proposals, or a `Depends-On:` body line. Bare `project#N` refs can be
  shadowed when an issue and proposal share a number; prefer
  `project#proposal:N` for not-yet-adopted proposals.
- If a direct issue was created in B by mistake, recover by sending the proposal
  from A, then close the mistaken B issue as superseded with
  `issuekit complete <id> --force --summary "Superseded by proposal <ref>"`
  and an audit-style verification note.

Authoring constraints:

- All author-supplied workflow text must be ASCII-only: issue and proposal
  title and body, review summary/verification/notes, and edit/append text.
  Write bodies in English and check before submitting; non-ASCII input
  (em dashes, curly quotes, non-English characters) is rejected at author,
  propose, edit, submit-review, request-changes, approve, and complete.
- Mentioning another configured project ref in a local issue body triggers the
  cross-project preflight and blocks direct creation with `issuekit author`.
  Decision rule: if the change belongs to the other project, send
  `issuekit propose --to <project>`; if the issue is genuinely local and only
  references the other project, rerun with `--direct-local-author`.

Separation-of-duties invariants:

- The author role and implementer role must be different sessions. If the same
  agent name appears through the open implement pool, it represents a distinct
  operator/session; explicit author self-assignment is rejected.
- After `issuekit author` or `issuekit propose` succeeds, issuekit writes a
  machine-local author-session guard and emits `STOP_NOW`. Issue guards block
  implementing that authored issue in the same checkout; proposal guards record
  the handoff but do not block unrelated local issue lifecycle work.
- The implementer and reviewer must be different sessions; explicit implementer
  self-review is rejected.
- The author may also be the reviewer when a different implementer did the work.

Canonical guard diagnostics: see README.md#separation-of-duties-guards or run
`issuekit author-guard --help` to diagnose which guard blocked a command.

{SEPARATION_GUARD_REFERENCE}

Copyable CLI examples:

- Register worker: `issuekit add`
- Author: `issuekit author --title "Short title" --body-file issue.md --priority medium --agent <agent>`
- Author with upstream dependency: `issuekit author --title "Short title" --body-file issue.md --priority medium --agent <agent> --depends-on upstream#proposal:123`
- Author a local issue that references another project: `issuekit author --title "Short title" --body-file issue.md --direct-local-author`
- Claim next: `issuekit claim --assignee <agent>`
- Claim specific issue: `issuekit claim --id 123 --assignee <agent>`
- Submit review: `issuekit submit-review 123 --summary "Implemented." --branch main --commit abc123`
- Agent review: `issuekit review 123 --agent <agent>`
- Request changes: `issuekit request-changes 123 --notes "Add focused tests." --reviewer <agent>`
- Approve: `issuekit approve 123 --verification "uv run pytest" --reviewer <agent>`
- Complete: `issuekit complete 123 --summary "Done." --verification "uv run pytest"`
- Close no-op issue: `issuekit complete 123 --force --summary "Obsolete." --verification "no local code scope"`
- Blocking proposal: `issuekit propose --to <project> --title <t> --body <b> --blocking --json`
- Proposal with upstream dependency: `issuekit propose --to <project> --title <t> --body <b> --depends-on upstream#proposal:123 --json`
- Incoming proposals: `issuekit incoming --json`
- Adopt proposal: `issuekit adopt 42 --priority medium --json`
- Outgoing proposal status: `issuekit outgoing --to <project> --json`
- Serve with target-owned inbox triage: `issuekit serve --agent <agent> --triage`
- Serve as a reviewer worker: `issuekit serve --agent <agent> --review`
"""


TRIAGE_PROTOCOL = """# Handoff protocol (triage)

The triage role reviews this project's incoming proposal inbox and decides
what enters the issue queue. Run it on a schedule or whenever asked to check
or triage proposals.

1. List pending proposals with `issuekit incoming --json` (MCP
   `list_incoming`). If the inbox is empty, stop.
2. Evaluate each proposal on four axes before deciding: value (fixes a real
   defect, removes friction, or unblocks another project), fit (belongs in
   this project rather than the origin or a third project), dependencies
   (referenced upstream proposals or issues exist and are accepted or tracked),
   and cost (a simple, well-scoped implementation exists). Read the referenced
   code and check whether the change already landed before judging. Perform a
   code-verified review: verify each factual claim the proposal makes about
   this codebase, including named functions, guards, config keys, validation
   patterns, and existing helpers, and note claims that are wrong or already
   implemented. Identify design decisions the proposal leaves open, such as
   ordering constraints, skip-vs-hard-fail behavior, escape hatches, and command
   shape among proposed alternatives, and resolve each with a recommendation
   grounded in existing code conventions.
3. Adopt worthwhile proposals with `issuekit adopt <id> --priority <p>
   --append-file <file> --json` or MCP `adopt_proposal(append=...)`. Fold the
   verified findings and resolved decisions into the adopted issue body through
   that append surface, using an ASCII English section headed
   `## Reviewer design decisions (<date>, verified against current code)`.
   When several pending proposals interact, state the recommended
   implementation order in each adopted body. Adoption creates an active issue
   in the open implement pool; keep one issue per proposal and do not merge
   unrelated proposals.
4. Discard proposals that are already implemented, duplicates, consumed
   negotiation-thread entries, or out of scope with `issuekit discard <id>`.
   If a downstream proposal is missing a required upstream prerequisite,
   either leave it pending until the referenced upstream issue exists, discard
   it as premature, or send a reply explaining the upstream proposal that must
   be created first. Recreate the downstream proposal later with
   `--depends-on` once the upstream reference exists.
   When the origin project needs to know why, send a reply proposal with the
   reasoning instead of leaving the decision implicit.
5. Do not implement adopted issues in the triage session. Implementers claim
   them through the normal cycle, or an orchestrator drives
   `issuekit implement <id> --agent <agent>`.

Projects may automate trusted target-owned triage by configuring
`[triage] trusted_origins`, `default_priority`, `require_blocking`, and
`max_adoptions_per_cycle`, then running `issuekit serve --triage`. Each serve
poll first auto-adopts matching pending proposals, then claims and implements
through the normal review-gated cycle. Use `issuekit propose --blocking` for
hard cross-project dependencies when the target requires blocking proposals.
"""


PM_PROTOCOL = """# Handoff protocol (pm)

The PM role receives user development requests that may span projects, routes
them to owning projects as thin proposals, and then stops. A PM checkout has
its own registered worker identity and API project. It proposes only; it never
claims, implements, reviews, approves, or completes work.

1. Register the dedicated PM checkout with `issuekit add`.
2. Route a new request with `issuekit request "<text>"`. The router reads
   project capability profiles, excludes stale profiles and the PM project,
   and sends one or more dependency-first proposals to target projects.
3. If the router asks for clarification before routing, answer in the same PM checkout with
   `issuekit request --answer <request-id> "<answer>"`. Clarifications are
   synchronous and stay in the request state; do not turn them into proposals.
4. If a target project replies for clarification, list PM inbox questions with
   `issuekit request --inbox`, then answer with
   `issuekit request --answer <request-id> "<answer>" --target <project>` when
   more than one target has a pending question. The PM resends an amended
   proposal with a `Supersedes:` line and discards the answered PM inbox reply.
5. Track what happened with `issuekit request --status <request-id>` or list
   all routed requests with `issuekit request --status --json`. Status reads
   outgoing proposal state so the requester can see pending, adopted, or
   discarded target proposals and adopted issue refs.
6. If the router rejects the request, report the reason and stop. If the
   request exceeds the configured target cap, ask one concrete clarification
   question or reject it.

Copyable CLI examples:

- Register PM checkout: `issuekit add`
- Route request: `issuekit request "Add dashboard export support"`
- Dry run routing: `issuekit request "Add dashboard export support" --dry-run --json`
- Answer clarification: `issuekit request --answer 7 "CSV export is enough for v1."`
- List target questions: `issuekit request --inbox`
- Answer target question: `issuekit request --answer 7 "CSV export is enough for v1." --target api`
- Check one request: `issuekit request --status 7`
- Check all requests: `issuekit request --status --json`

PM invariants:

- Do not run `issuekit claim`, `issuekit implement`, `issuekit submit-review`,
  `issuekit request-changes`, `issuekit approve`, or `issuekit complete`.
- Do not mutate target project issue lifecycle state directly. Target projects
  own inbox triage and turn thin proposals into implementation-ready issues.
- Work dependency-first: upstream API or contract owners receive proposals
  before downstream consumers, and downstream proposals reference the upstream
  proposal or issue with `--depends-on` semantics.
"""


IMPLEMENTER_PROTOCOL = """# Handoff protocol (implementer)

The implementer handles issuekit tasks from the API-backed project queue. Any
configured agent can be the implementer or the reviewer. The reviewer is the
agent assigned at stage=review and defaults to `default_reviewer`, which may be
`auto`.
Same-name review is allowed through the open review pool by omitting `reviewer`;
an implementer may not explicitly assign itself as reviewer at submit time.

Cross-project proposals are API inbox entries.
Before claiming normal work, inspect `issuekit incoming` when cross-repo
exchange is relevant. Adopt proposals only after local triage. When completing
an adopted issue with an `origin:` field, optionally send `issuekit propose
--reply <id>` so the origin repo receives a new inbound proposal; do not mutate
state in the origin repo.

When work reveals that a needed change belongs to another project,
originate a proposal instead of only working around it locally or reporting it.
Use `issuekit propose --to <project> --title <t> --body <b>` (or the MCP
`propose` tool). Proposals are non-destructive suggestions in the target
project's API inbox; the target project owns triage, so do not mutate its state
directly. Add `--blocking` for hard cross-project dependencies so trusted
targets can restrict auto-adoption to blocking proposals.

Write maintainable, idiomatic code. Match the surrounding file's import style,
naming, and comment density. Use normal imports and real identifiers when they
work; use `importlib`, `getattr`, `setattr`, or `globals()` attribute injection
only when dynamic loading is actually required. Do not split, concatenate, or
otherwise obfuscate string literals, import paths, or identifiers to avoid plain
source text. Passing tests is not enough if the implementation is needlessly
hard to read or maintain.

For multi-project changes, work dependency-first. Identify the project that
owns the first required contract or API change, create or propose that upstream
work before downstream consumer work, and include
`--depends-on <project#N|project#issue:N|project#proposal:N>` (or `Depends-On:`
in the body) on later downstream local issues or proposals. Bare `project#N`
refs can be shadowed when an issue and proposal share a number; prefer
`project#proposal:N` for not-yet-adopted proposals. If the body says it depends
on another project and no upstream reference is supplied, proposal preflight
warns but does not block the send.

The API skips `dependency_state=waiting` and `dependency_state=attention`
issues when claiming the next task. Do not manually pick waiting issues from
queue output. If an explicit claim returns a dependency warning, read the
upstream ref first and only proceed when the warning is understood.

Proposal-system MCP and CLI share one implementation, so the CLI is a drop-in
fallback when the MCP tools hang or error. Equivalents (add `--json` for the
same structured output the MCP tools return):

- `propose(to, title, body)` -> `issuekit propose --to <project> --title <t> --body <b> --json`
- `propose(to, title, body, blocking=True)` -> `issuekit propose --to <project> --title <t> --body <b> --blocking --json`
- `propose(to, title, body, depends_on="upstream#proposal:123")` -> `issuekit propose --to <project> --title <t> --body <b> --depends-on upstream#proposal:123 --json`
- `list_incoming()` -> `issuekit incoming --json`
- `adopt_proposal(proposal_id, priority)` -> `issuekit adopt <id> --priority <p> --json`

When the user asks an implementer to work on an issue in open-ended terms, such
as "handle the next issue" or "take the queue", do not wait for explicit
commands. Run this protocol end to end:

1. Call the issuekit MCP tool `claim_next_task()`, which resolves the
   implementer from `default_implementer`; use
   `claim_next_task(assignee="<agent>")` for an explicit implementer. The
   returned payload includes the issue body, which is the spec to implement. If
   it returns no issue, report that the queue is empty and stop.
2. Read the claimed issue, especially Problem, Implementation Plan, and Test
   Plan. Lay out a short plan with the files to change and the order of steps.
   Confirm the plan matches the issue scope before writing code; do not expand
   beyond it.
3. Implement the claimed issue on the current branch by editing only the code,
   tests, and supporting project files needed for the task. Do not create or
   switch branches. When driven by `issuekit implement`, do not run git commit
   or git push; leave implementation changes unstaged for review.
4. Run the relevant tests and `uv run issuekit check-encoding`.
5. Call `submit_for_review(id, summary, branch, commit, reviewer=None)` with an
   ASCII summary and optional branch/commit metadata. Omit reviewer to use
   `default_reviewer`, or pass another configured assignee. If
   `default_reviewer` is `auto`, the issue enters the open review pool so any
   agent (including another session of the same name) may review it. An
   implementer may not name itself as the explicit reviewer; use the open pool
   for same-name review.
6. If a reviewer returns the issue with stage=changes_requested, call
   `claim_next_task()` again, or use `claim_next_task(assignee="<agent>")` for
   an explicit implementer, read the Review Feedback note, re-plan for just
   that feedback, address it, and submit for review again.

The assigned implementer owns implementation unless assigned as reviewer. The
reviewer owns the review decision for issues assigned to them at stage=review.
"""


AUTHOR_PROTOCOL = """# Handoff protocol (author)

An author writes implementation-ready issues and proposals. The author does not
implement issues.

When a needed change belongs to another project, originate a proposal instead
of only reporting it. Use `issuekit propose --to <project> --title <t> --body
<b>` (or the MCP `propose` tool). Proposals are non-destructive suggestions in
the target project's API inbox; the target project owns triage, so do not mutate
its state directly. Add `--blocking` when the proposal is a hard dependency.
Do not `cd` into the target project and run `issuekit author`; that makes the
target queue look like the work originated locally and bypasses proposal triage.

For multi-project changes, work dependency-first. Identify the project that
owns the first required contract or API change, create or propose that upstream
work before downstream consumer local issues or proposals, then reference the
upstream item with
`--depends-on <project#N|project#issue:N|project#proposal:N>` or a
`Depends-On:` body line. Bare `project#N` refs can be shadowed when an issue and
proposal share a number; prefer `project#proposal:N` for not-yet-adopted
proposals. If a downstream proposal body says it depends on another project but
lacks that reference, issuekit warns so the author can create the upstream
proposal first.

When the proposal-system MCP tools hang or error, fall back to the equivalent
CLI: `issuekit propose --to <project> --title <t> --body <b> --json`,
`issuekit propose --to <project> --title <t> --body <b> --blocking --json`,
`issuekit propose --to <project> --title <t> --body <b> --depends-on upstream#proposal:123 --json`,
`issuekit incoming --json`, and `issuekit adopt <id> --json`. They share the
same implementation and emit the same structured output.

When asked to write or plan an issue:

1. First decide whether this is local work. If it originates in another project,
   use `issuekit propose --to <project>` from the origin project instead.
2. Create local issues with `issuekit author`; the API allocates the issue id.
   When local work depends on an upstream issue or proposal that already
   exists, pass
   `--depends-on <project#N|project#issue:N|project#proposal:N>` so
   implementers can see and respect the dependency state. Use
   `project#proposal:N` for not-yet-adopted proposals. When `ISSUEKIT_SESSION` is set,
   issuekit records that authoring session so a later same-name delegated
   implementer can be distinguished by session.
3. Leave the issue unstarted with no assignee unless a specific implementer is
   required.
4. STOP_NOW. The command writes a local author-session guard for that issue.
   Do not call `claim_next_task`, `issuekit claim`, or `submit_for_review` for
   the authored issue in the same session. An implementer claims it later via
   `claim_next_task`.
5. If the author is coordinating the handoff, the author may run
   `issuekit implement <id> --agent <agent>` for the authored issue. This does
   not make the author the implementer: issuekit creates a separate run session,
   exports it to the child agent as `ISSUEKIT_SESSION`, and uses that same token
   for the claim and submit mutations. Use a different configured agent when
   possible; same-name delegation is accepted only when the recorded author
   session and launched run session are both present and differ.

After `issuekit propose` succeeds, treat the `STOP_NOW` sentinel the same way:
stop the author session and let the target project triage the proposal. Proposal
guards are retained as handoff records but do not block unrelated local issue
lifecycle commands. For recovery from an accidental guard after handoff, run
`issuekit author-guard clear`. Human emergency lifecycle commands can pass
`--allow-author-session`.
"""


REVIEWER_PROTOCOL = """# Handoff protocol (reviewer)

The reviewer handles issuekit tasks after an implementer submits them for
review. Any configured reviewer can use this flow. The reviewer is the agent
assigned at stage=review and defaults to `default_reviewer`, which may be
`auto`. Same-name review is allowed through the open review pool; an
implementer may not explicitly assign itself as reviewer at submit time.

When review reveals that a needed change belongs to another project, originate
a proposal instead of only reporting it. Use `issuekit propose --to <project>
--title <t> --body <b>` (or the MCP `propose` tool). Proposals are
non-destructive suggestions in the target project's API inbox; the target
project owns triage, so do not mutate its state directly. Add `--blocking`
when the proposal is a hard dependency.

If review uncovers a multi-project chain, work dependency-first: record the
upstream contract or API owner first. Send downstream consumer proposals only
after that upstream issue or proposal exists, and reference it with
`--depends-on <project#N|project#issue:N|project#proposal:N>` or a
`Depends-On:` body line. Use `project#proposal:N` for not-yet-adopted proposals.

1. Call the issuekit MCP tool `next_review(reviewer=None)`. Omit reviewer to
   use `default_reviewer`, or pass the reviewer assignee to inspect. With
   `default_reviewer = "auto"`, omitted reviewer means the next issue already
   assigned at stage=review.
2. Review the referenced branch and commit diff against the issue body. For an
   automated one-shot review, run `issuekit review <id> --agent <reviewer>`.
   Treat readability and maintainability as review criteria alongside
   correctness. Request changes for gratuitous obfuscation, string-concatenated
   identifiers or import paths, avoidable `importlib`/`getattr` indirection,
   `globals()`/`setattr` attribute injection where a plain definition works, or
   unexplained style deviations, even when tests pass.
3. If the implementation is acceptable, approve it through the reviewer flow:
   call `approve(id, verification, reviewer=None)` with ASCII verification, or
   use the CLI `issuekit approve <id> --verification <text>` command. The CLI
   `issuekit complete <id>` command remains available when a completion summary
   is needed. Use `issuekit complete <id> --force --summary <text>
   --verification <text>` to close an active no-op, duplicate, obsolete, or
   anchor issue without creating a fake implementation and review cycle.
4. If changes are needed or the work is incomplete, call
   `request_changes(id, notes, reviewer=None, assignee=None)` with ASCII notes.
   Omit assignee to return the issue to its recorded implementer.

Authors own proposals and implementation-ready issues unless assigned as
implementer. The assigned reviewer owns the review decision. The approving
session or agent must not be the same session that implemented the issue;
same-name review is allowed only when the issue was routed through the open
review pool.

To run continuously as a reviewer worker, use a separate registered checkout:
`issuekit serve --agent <reviewer> --review`.

When the proposal-system MCP tools hang or error, fall back to the equivalent
CLI: `issuekit propose --to <project> --title <t> --body <b> --json`,
`issuekit propose --to <project> --title <t> --body <b> --blocking --json`,
`issuekit propose --to <project> --title <t> --body <b> --depends-on upstream#proposal:123 --json`,
`issuekit incoming --json`, and `issuekit adopt <id> --json`. They share the
same implementation and emit the same structured output.
"""


_ROLE_PROTOCOLS = {
    "author": AUTHOR_PROTOCOL,
    "implementer": IMPLEMENTER_PROTOCOL,
    "pm": PM_PROTOCOL,
    "reviewer": REVIEWER_PROTOCOL,
    "triage": TRIAGE_PROTOCOL,
}

_AGENT_ROLE = {
    "codex": "implementer",
    "claude": "reviewer",
}


def effective_agent_roles(
    agent_roles: dict[str, str] | None = None,
    agent_names: Iterable[str] = (),
) -> dict[str, str]:
    """Return configured protocol roles with built-in defaults included."""
    return (
        {name: "implementer" for name in agent_names}
        | _AGENT_ROLE
        | (agent_roles or {})
    )


SERVER_INSTRUCTIONS = """# Role-specific instructions

To see the full protocol steps for your role, call:

- `get_protocol(role="author")` for the author protocol
- `get_protocol(role="implementer")` for the implementer protocol
- `get_protocol(role="pm")` for the PM router protocol
- `get_protocol(role="reviewer")` for the reviewer protocol
- `get_protocol(role="triage")` for the proposal-inbox triage protocol
"""


def render_protocol(
    agent: str | None = None,
    role: str | None = None,
    agent_roles: dict[str, str] | None = None,
) -> str:
    """Render the handoff protocol for one agent/role, or all roles."""
    if agent is None and role is None:
        return "\n\n".join(
            (
                CYCLE_PROTOCOL.rstrip(),
                AUTHOR_PROTOCOL.rstrip(),
                IMPLEMENTER_PROTOCOL.rstrip(),
                PM_PROTOCOL.rstrip(),
                REVIEWER_PROTOCOL.rstrip(),
                TRIAGE_PROTOCOL,
            )
        )
    if role is not None:
        try:
            role_protocol = _ROLE_PROTOCOLS[role]
        except KeyError as exc:
            raise ValueError(f"unknown role: {role}") from exc
        return f"{CYCLE_PROTOCOL.rstrip()}\n\n{role_protocol}"
    resolved_role = effective_agent_roles(agent_roles).get(agent, "implementer")
    return f"{CYCLE_PROTOCOL.rstrip()}\n\n{_ROLE_PROTOCOLS[resolved_role]}"


def render_server_instructions() -> str:
    """Render lean server instructions: cycle overview plus a get_protocol pointer."""
    return f"{CYCLE_PROTOCOL.rstrip()}\n\n{SERVER_INSTRUCTIONS}"
