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

Machine-readable output: With `--json`, stdout contains exactly one JSON
document; warnings and diagnostics go to stderr. Trust the exit status: non-zero
means the request was not fully applied, even if JSON was printed (for example,
`payload_mismatch` or `append_error`). Do not merge stderr into stdout (`2>&1`)
before parsing or let a pipeline (`| grep`, `| tail`) hide the command status;
check it, for example with `${{PIPESTATUS[0]}}`. After a parse failure for
`author` or `propose`, do not retry until `issuekit queue` or
`issuekit outgoing --to <project>` confirms whether the first call created the item.

The model is pull-based: authors publish work to a pool, implementers pull from
that pool, and reviewers pull from the review pool. No central orchestrator is
required for the normal author -> implement -> review cycle.

Register each checkout once with `issuekit add` (alias `issuekit register`)
before pulling work. It records repo_id, worker_name, and machine metadata in a
gitignored `issuekit.local.toml`, so claims report which worker checkout holds
an issue as `worker.repo@machine`. Multiple checkouts of one repo can use
distinct worker names.

Assignment chooses the implementing agent. Direction chooses the checkout that
must run host-specific or checkout-specific work. Use `issuekit author
--target-worker <worker.repo[@machine]>` when creating that work, or `issuekit
dispatch <id> --target-worker <worker.repo[@machine]>` for an existing issue at
`planned`, `todo`, or `changes_requested`. Use `issuekit readdress <id>` to
return directed work to the open pool.

Issue lifecycle and cross-project proposal state are stored in the configured
mine-py API project.

After `Transport closed`, MCP stdio is dead even if metadata remains. Until
restart, use `issuekit protocol --role <role>`,
`issuekit incoming --json`, `issuekit info --json`, `issuekit show <id> --json`,
or `issuekit next-review --json`.

Proposal-system CLI fallback: For MCP errors or hangs, use these commands with
`--json`:

- `issuekit propose --to <project> --title <t> --body <b> --json`
- `issuekit propose --to <project> --title <t> --body <b> --blocking --json`
- `issuekit propose --to <project> --title <t> --body <b> --depends-on upstream#proposal:123 --json`
- `issuekit incoming --json`
- `issuekit adopt <id> --json`
- `issuekit adopt <id> --priority <p> --json`

When an orchestrator or author needs to drive a configured external
implementer instead of waiting for the pull model, use
`issuekit implement <id> --agent <agent> --timeout-sec <n>`. That command
claims or operates on the assigned issue, launches the configured agent, and
submits the completed work for review. This is a sanctioned orchestration path:
the parent session launches a distinct implementer run session and records the
orchestrator in the submit summary. `ISSUEKIT_SESSION` is passed to the child
for both claim and submit mutations. It is different from
`--allow-author-session`, which is only a human emergency bypass for a local
STOP guard. Prefer a clean worktree before orchestrating so existing author
edits are not attributed to the implementer run.
Its `--follow` heartbeat polls `git status` read-only without an index lock, so
it is safe for issues that rewrite the checkout.

Operators: agent flags, models and roles are configured as described in
the issuekit repository's `docs/guides/configuration.md`.

Commands and MCP tools that omit an implementer resolve it as an explicit value,
then `default_implementer`, then the single enabled assignee. They fail with a
clear message when more than one enabled assignee exists and no default is set.
When a reviewer daemon is needed, run it from a separate registered checkout:
`issuekit serve --agent <reviewer> --review`. It can review only committed and
pushed changes it can see, or evidence-only host and verification submissions.
For a one-shot review, use `issuekit review <id> --agent <reviewer>` in the
checkout that holds the implementation diff.

Upstream feedback loop: report issuekit bugs, limitations, or improvements from
any project before finishing with `issuekit propose --to issuekit` (or MCP
`propose`). Include a reproduction or concrete gap; pass `--from-issue <id>` for
issue-specific reports and check status with `issuekit outgoing --to issuekit`.
The issuekit project triages reports and adopts worthwhile ones. Adoption notes
are recorded only on the receiving project's issue and never reach the sender,
so send a proposal for follow-up. The proposal guard records the handoff without
interrupting a current implementation or review task; continue that task unless
sending the proposal was your only task. Automated triage may use
`adopt_and_reply`; discard does not notify.

Use `propose` for specified changes owned elsewhere; use `negotiate` for
undecided interfaces. Negotiation is CLI-only; MCP only inspects threads. Both
sides run read-only, and issuekit rejects worktree, HEAD, or branch changes.
`--counterpart-ref` selects the real counterpart checkout. Finalize agreed
threads with `issuekit negotiate --finalize <thread_id>` to create linked issues.
`--from-proposal` seeds consumer-side work and locks the proposal until atomic
finalization or `--cancel`; it requires proposal-negotiation API support.
Otherwise use `--from-issue`.
See the issuekit repository's `docs/guides/negotiation.md`.

Local issues vs. cross-project proposals:

- Use `issuekit author` only for local work. If project B owns a change found
  from project A, stay in A and propose it to B; do not `cd` to B and use
  `author`, which bypasses proposal triage.
- **Dependency-first multi-project work:** Identify the project that owns the
  first required contract or API change. Create or propose it before downstream
  consumer work; reference later issues or proposals with
  `--depends-on <project#N|project#issue:N|project#proposal:N>` or
  `Depends-On:`. Bare `project#N` refs can be shadowed when issue and proposal
  numbers overlap; use `project#proposal:N` for pending proposals. Missing refs
  produce a warning but do not block sending.
- If a direct issue was created in B by mistake, recover by sending the proposal
  from A, then close the mistaken B issue with
  `issuekit complete <id> --force --summary "Superseded by proposal <ref>"`
  and an audit-style verification note.

Authoring constraints:

- Workflow text supplied to issuekit must be ASCII-only and English: issue or
  proposal titles and bodies, review summary/verification/notes, and edit or
  append text. Non-ASCII is rejected by author, propose, edit, submit-review,
  request-changes, approve, and complete.
- A target inbox allows one pending proposal per origin
  `<project>#<id>@<commit>`. `--from-issue` and `--reply` distinguish source
  issues, not proposals. A different second payload exits 1 with
  `payload_mismatch: true`. To send separately, omit `--from-issue` (implicit
  `#0`; omit `--reply` too to drop its link) or resolve the pending proposal.
- Naming another configured project ref in a local issue body triggers
  preflight and blocks `issuekit author`. Propose to the owner, or use
  `--direct-local-author` when the work is local and only references that
  project.

Separation-of-duties invariants:

- Authors and implementers must use different sessions; an open-pool same-name
  implementer is a distinct operator/session, and explicit author self-assignment
  is rejected.
- `issuekit author` writes an issue guard and emits `STOP_NOW`. It blocks direct
  lifecycle work on that issue and pool claims from the checkout until
  `issuekit author-guard clear`; proposal guards do not block local issue
  lifecycle work.
- A proposal guard records the handoff without interrupting current
  implementation or review; stop only when sending the proposal was your only
  task. Implementers and reviewers must use different sessions, and explicit
  self-review is rejected. An author may review work done by another
  implementer.

Canonical guard diagnostics: see the issuekit repository's
`docs/guides/separation-of-duties.md` or run `issuekit author-guard --help` to
diagnose which guard blocked a command.

{SEPARATION_GUARD_REFERENCE}

For command syntax and copyable CLI examples, see the issuekit repository's
`docs/guides/commands.md`.

Copyable CLI examples:

- Author: `issuekit author --title "Short title" --body-file issue.md --priority medium --agent <agent>`
- Author for a worker: `issuekit author --title "Short title" --body-file issue.md --agent <agent> --target-worker <worker.repo@machine>`
- Dispatch: `issuekit dispatch 123 --target-worker <worker.repo@machine> --json`
- Readdress: `issuekit readdress 123 --json`
- Author with dependency: `issuekit author --title "Short title" --body-file issue.md --priority medium --agent <agent> --depends-on upstream#proposal:123`
- Author local cross-ref: `issuekit author --title "Short title" --body-file issue.md --agent <agent> --direct-local-author`
- Claim next: `issuekit claim --assignee <agent>`
- Claim by id: `issuekit claim --id 123 --assignee <agent>`
- Submit review: `issuekit submit-review 123 --summary "Implemented." --branch main --commit abc123`
- Review: `issuekit review 123 --agent <agent>`
- Request changes: `issuekit request-changes 123 --notes "Add focused tests." --reviewer <agent>`
- Request changes from file: `issuekit request-changes 123 --notes-file <notes.md> --reviewer <agent>`
- Approve: `issuekit approve 123 --verification "uv run pytest" --reviewer <agent>`
- Complete: `issuekit complete 123 --summary "Done." --verification "uv run pytest"`
- Complete no-op: `issuekit complete 123 --force --summary "Obsolete." --verification "no local code scope"`
- Outgoing status: `issuekit outgoing --to <project> --json`
- Serve triage: `issuekit serve --agent <agent> --triage`
- Serve reviewer: `issuekit serve --agent <agent> --review`
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

For proposal-system CLI equivalents, see the Proposal-system CLI fallback list
in the delegation cycle overview.
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
- For multi-project work, follow the dependency-first rule in the delegation
  cycle overview above.

For proposal-system CLI equivalents, see the Proposal-system CLI fallback list
in the delegation cycle overview.
"""


IMPLEMENTER_PROTOCOL = """# Handoff protocol (implementer)

When `issuekit implement` or `issuekit serve` launched you, issuekit has
already claimed the issue and will submit it: skip steps 1, 5 and 6 and do not
call `claim_next_task` or `submit_for_review`.

The implementer handles issuekit tasks from the API-backed project queue. Any
configured agent can be the implementer or the reviewer. The reviewer is the
agent assigned at stage=review and defaults to `auto` in API mode.

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

For multi-project work, follow the dependency-first rule in the delegation
cycle overview above.

The API skips `dependency_state=waiting` and `dependency_state=attention`
issues when claiming the next task. Do not manually pick waiting issues from
queue output. If an explicit claim returns a dependency warning, read the
upstream ref first and only proceed when the warning is understood.

For proposal-system CLI equivalents, see the Proposal-system CLI fallback list
in the delegation cycle overview.

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
   or git push; leave implementation changes unstaged for review. When
   `ISSUEKIT_IMPLEMENTER_REPORT_FILE` is set, write the closing implementation
   and verification report to that path; the report is mandatory and a run
   that ends without one is refused at submit time, the same way the submit
   gate refuses encoding violations. Include a section
   listing every acceptance criterion you could not verify in this
   environment and why (for example: no browser available in a headless run);
   leaving such a criterion unmentioned is worse than reporting it unverified.
   Issuekit sanitizes the report to ASCII, bounds its length, and includes it
   in the submit summary.
4. Run the relevant tests and `issuekit check-encoding --gate` before
   submitting. This run is a
   single turn: background tasks cannot wake you, no completion notification
   will ever arrive, and ending the turn ends the run. Await every
   verification command you start and record its actual exit result; starting
   a command in the background and ending the turn is a failed run, not a
   completed one.
5. Call `submit_for_review(id, summary, branch, commit, reviewer=None)` with an
   ASCII summary and optional branch/commit metadata. Omit reviewer to use
   `default_reviewer`, or pass another configured assignee. If
   `default_reviewer` is `auto`, the issue enters the open review pool so any
   eligible reviewer may review it.
6. If a reviewer returns the issue with stage=changes_requested, call
   `claim_next_task()` again, or use `claim_next_task(assignee="<agent>")` for
   an explicit implementer, read the Review Feedback note, re-plan for just
   that feedback, address it, and submit for review again.

When a change rewrites a test file or removes any test case, list the before
and after test names for that file in the report and justify each removal. A
rising suite total is not evidence that no coverage was lost; both can be true
at once.

A test name is a claim: if it says a state or behavior occurs, the test body
must assert that state, not just exercise the surrounding code path.
Otherwise, rename the test to what it actually checks. When a mocked
dependency resolves too quickly to observe an intermediate state, use a
manually-resolved promise, not a weaker name.

When a change corrects a misconception, check whether any comment in the
touched files restates it. Do not replace an accurate comment with one that is
weaker or repeats the misconception the change was meant to fix.

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

For multi-project work, follow the dependency-first rule in the delegation
cycle overview above.

For proposal-system CLI equivalents, see the Proposal-system CLI fallback list
in the delegation cycle overview.
Request target-side evaluation before adoption with `issuekit
proposal-check-request --to <project> --proposal <id>` or the MCP
`create_proposal_check` tool.

When asked to write or plan an issue:

1. First decide whether this is local work. If it originates in another project,
   use `issuekit propose --to <project>` from the origin project instead.
2. Create local issues with `issuekit author`; the API allocates the issue id.
   When local work depends on an upstream issue or proposal that already
   exists, pass
   `--depends-on <project#N|project#issue:N|project#proposal:N>` so
   implementers can see and respect the dependency state. Use
   `project#proposal:N` for not-yet-adopted proposals. issuekit records an
   authoring session, using `ISSUEKIT_SESSION` when set or generating one
   otherwise, so a later same-name delegated implementer can be distinguished
   by session.
   Include `issuekit check-encoding --gate` in the Test Plan so the
   implementer verifies the submit-gate verdict before submission.
3. Leave the issue unstarted with no assignee unless a specific implementer is
   required.
4. STOP_NOW. The command writes a local author-session guard for that issue.
   Do not call `claim_next_task`, `issuekit claim`, or `submit_for_review` for
   the authored issue in the same session. An implementer claims it later via
   `claim_next_task`.
5. For author-coordinated implementation, use the sanctioned orchestration path
   in the delegation cycle overview above.

After `issuekit propose` succeeds, let the target project triage the proposal.
The proposal guard records the handoff but does not interrupt your current task;
stop only if sending the proposal was your only task. Proposal guards do not
block unrelated local issue lifecycle commands. For recovery from an accidental
guard after handoff, run `issuekit author-guard clear`. Human emergency
lifecycle commands can pass `--allow-author-session`.
"""


REVIEWER_PROTOCOL = """# Handoff protocol (reviewer)

The reviewer handles issuekit tasks after an implementer submits them for
review. Any configured reviewer can use this flow. The reviewer is the agent
assigned at stage=review and defaults to `auto` in API mode.

When review reveals that a needed change belongs to another project, originate
a proposal instead of only reporting it. Use `issuekit propose --to <project>
--title <t> --body <b>` (or the MCP `propose` tool). Proposals are
non-destructive suggestions in the target project's API inbox; the target
project owns triage, so do not mutate its state directly. Add `--blocking`
when the proposal is a hard dependency.

For multi-project dependencies found during review, follow the dependency-first
rule in the delegation cycle overview above.

1. Call the issuekit MCP tool `next_review(reviewer=None)`. Omit reviewer to
   use `default_reviewer`, or pass the reviewer assignee to inspect. With
   `default_reviewer = "auto"`, omitted reviewer means the next issue at
   stage=review, whether assigned or in the open review pool. If MCP is
   unavailable, use the read-only CLI
   fallback `issuekit next-review [--reviewer <name>] --json`, then use
   `issuekit show <id> --json` to reread a specific issue.
2. Review the implementation diff in the checkout that holds it (the
   implementer leaves changes unstaged); never approve code changes from the
   handoff text alone. For an automated one-shot review, run
   `issuekit review <id> --agent <reviewer>`.
   Treat readability and maintainability as review criteria alongside
   correctness. Request changes for gratuitous obfuscation, string-concatenated
   identifiers or import paths, avoidable `importlib`/`getattr` indirection,
   `globals()`/`setattr` attribute injection where a plain definition works, or
   unexplained style deviations, even when tests pass.
3. If the implementation is acceptable, approve it through the reviewer flow:
   call `approve(id, verification, reviewer=None)` with ASCII verification, or
   use the CLI `issuekit approve <id> --verification <text>` command. Pass
   `--summary <text>` to `issuekit approve` when a completion summary is
   needed. Use `issuekit complete <id> --force --summary <text>
   --verification <text>` to close an active no-op, duplicate, obsolete, or
   anchor issue without creating a fake implementation and review cycle.
   After `approve`, the approving or orchestrating session commits the approved
   working-tree changes with the issue ref in the commit message.
4. If changes are needed or the work is incomplete, call
   `request_changes(id, notes, reviewer=None, assignee=None)` with ASCII notes.
   Omit assignee to return the issue to its recorded implementer. From a POSIX
   shell, notes with backticks or pipes can be rewritten before the CLI ever
   sees them; write the notes to a file and pass
   `issuekit request-changes <id> --notes-file <notes.md>` instead of
   `--notes` to avoid that.

Authors own proposals and implementation-ready issues unless assigned as
implementer. The assigned reviewer owns the review decision. The approving
session or agent must not be the same session that implemented the issue;

To run continuously as a reviewer worker, use a separate registered checkout:
`issuekit serve --agent <reviewer> --review`. It can review only committed and
pushed changes it can see, or evidence-only host and verification submissions.

For proposal-system CLI equivalents, see the Proposal-system CLI fallback list
in the delegation cycle overview.
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
        dict.fromkeys(agent_names, "implementer")
        | _AGENT_ROLE
        | (agent_roles or {})
    )


SERVER_INSTRUCTIONS = """Before acting, call `get_protocol(role="<role>")` for one role: author, implementer, reviewer, triage, pm.

The pull-based cycle is author, implement, then review: authors publish work, implementers claim and implement it, and reviewers decide it.

For commands with `--json`, stdout is one JSON document; trust the exit status, and treat non-zero as failure even if JSON was printed.

If MCP returns `Transport closed`, use the read-only CLI: `issuekit protocol --role <role>` and `issuekit show <id> --json`.

After `issuekit author` succeeds and prints `STOP_NOW`, stop that session. A
proposal guard does not interrupt an active implementer or reviewer task."""


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
    """Render a short pointer to the complete role-specific protocol."""
    return SERVER_INSTRUCTIONS
