# PM request router

`issuekit request` turns a natural-language development request into one or
more cross-project proposals. It runs the configured router agent against the
stored project profiles, then chooses one of three outcomes:

- `route`: send a proposal to each selected owning project;
- `clarify`: ask the requester one question before routing; or
- `reject`: report that the request is out of scope.

Unless `--dry-run` is present, a `route` decision sends its proposals for real.
Use `--dry-run` for a first invocation when you want to inspect the decision
without creating proposals.

## Run it from a PM checkout

Run the router from a dedicated PM checkout configured with `project = "pm"`.
This is the supported operating pattern: the PM checkout proposes work, but
does not claim, implement, review, approve, or complete it.

The router excludes the configured current project from its candidates. It
also excludes stale profiles. Running `request` from a product checkout would
therefore silently remove that product from the projects the router can select.

## Configuration

Configure the router in `[tool.issuekit.router]` for a Python project, or in
the equivalent `[router]` table in `issuekit.toml`:

```toml
[tool.issuekit]
project = "pm"

[tool.issuekit.router]
agent = "codex"
max_targets = 3
max_clarify_rounds = 2

[tool.issuekit.agents.codex.roles.router]
model = "gpt-6-sol"
reasoning_effort = "medium"
```

`agent` defaults to an empty value. New requests and pre-routing answers need a
configured router agent because `request` has no `--agent` flag. Status, inbox,
link, and target-project reply-answer operations do not use the router.
`max_targets` defaults to 3 and limits how many targets one route decision can
list. A decision with more targets is a parse error: the command fails without
sending anything instead of truncating the list. `max_clarify_rounds` defaults
to 2. After that many requester answers, issuekit calls the router with
`force_final`. If it still returns `clarify`, issuekit forcibly converts the
decision to a rejection with the reason
`Clarification limit reached and the router still requested clarification.`

`[agents.<name>.roles.router]` supplies the router role's model and reasoning
effort defaults. `--model` and `--reasoning-effort` override those defaults for
one run.

## Keep profiles current

Project profiles are the router's only candidate input. Inspect the local and
stored profile with:

```powershell
issuekit profile
issuekit profile --all --json
```

A project with no stored profile, or a profile marked stale, cannot receive
routed work. Use `issuekit profile --all --json` to inspect stale flags and all
stored profiles. The text output from `profile --all` omits stale status and
includes the PM project's own profile.

## Command surface

The normal form is:

```powershell
issuekit request "Add audit logging to the customer export"
```

The positional `text` is either the new development request, an answer passed
with `--answer`, or an existing proposal reference passed with `--link`.

- `--answer REQUEST_ID` answers a saved router question or a target project's
  clarification reply. Supply the answer as `text`.
- `--status [REQUEST_ID]` shows the recorded request and routed proposal
  statuses. With no id, it shows all saved requests.
- `--inbox` lists pending clarification replies from target projects in the PM
  proposal inbox. `--target` is ignored with `--inbox`. It cannot be combined
  with request text, `--answer`, `--status`, `--link`, or `--dry-run`; the
  rejection message currently lists its other conflicting flags but omits
  `--link`.
- `--target PROJECT` selects which target project's clarification is being
  answered. It is required with `--link`; use it with `--answer` when more than
  one target has a pending question.
- `--link REQUEST_ID` records an existing `project#id` proposal reference for
  an unsent target of a saved route. Use it to recover request state after a
  proposal exists but its reference was not recorded.
- `--json` prints structured output.
- `--dry-run` prints the router decision for a new request or pre-routing
  answer, without sending proposals or saving a decision. For a target-project
  reply answer, it prints an answer preview with the target and superseded
  proposal instead of running the router. It cannot be combined with
  `--status`, `--inbox`, or `--link`.
- `--timeout-sec SECONDS` sets the router agent's hard timeout (default 600).
- `--model MODEL_ID` and `--reasoning-effort VALUE` override the router agent
  settings for the run.

`--target` is valid with `--answer` or `--link`. `--link` also requires both
`--target` and the proposal reference as positional `text`.

## Route targets

In a route decision, as `--dry-run --json` shows it, each target names a
candidate project and supplies the proposal title and body. A target can also
set `blocking`, which becomes the proposal's blocking flag, and `depends_on`, a
list of dependency refs (`project#N`, `project#issue:N`, or
`project#proposal:N`) or `target:<index>` placeholders. A placeholder points at
an earlier target in the same decision by its zero-based position. Targets are
sent in order, so the placeholder resolves to that earlier target's sent
proposal ref, such as `project#proposal:N`. A placeholder that points at the
same or a later target is a parse error.

## Clarifications and saved state

The PM checkout stores request state in `.agent-runs/pm-requests.json`. This
state lets the command distinguish two similarly named flows:

1. Before routing, the router can return `clarify`. Answer that question with
    `issuekit request --answer REQUEST_ID "answer text"`. The router runs again
    with the saved question and answer. At the configured clarification limit,
    issuekit passes `force_final` to the router. If the router still returns
    `clarify`, issuekit converts it to a rejection with the reason
    `Clarification limit reached and the router still requested clarification.`
2. After routing, a target project's triage can send a proposal reply asking a
   question. Run `issuekit request --inbox` to find these target-side replies,
   then answer with `issuekit request --answer REQUEST_ID --target PROJECT
   "answer text"`. Issuekit sends an amended proposal to that target and
   records the clarification. The amended proposal keeps the original body,
   adds a `## Clarifications` section with every question and answer so far,
   and ends with `Supersedes: <old ref>`, so the target's
   [agent triage](cross-project-proposals.md#agent-triage) discards the old
   pending proposal when it adopts the new one. The request state switches to
   the new proposal ref, and the reply in the PM inbox is discarded.

`--inbox` is only for replies from target-project triage. It does not show a
pre-routing router question; that question is printed by the original request
and remains associated with its request id.

Each routed proposal carries an origin unique to its request and target,
`<pm-project>#request-<id>-target-<index>-<project>@<commit>`, so separate
requests can route to the same project while earlier proposals are still
pending there. The target index is fixed when the project is first routed.
Amended proposals use a different origin:
`<pm-project>#request-<id>-<project>-<prevId>-round-<n>@<commit>`, where
`<prevId>` is the prior proposal id and `<n>` is the clarification round.
Rerunning the same request text reuses its unfinished saved request id, matches
targets by project, and skips projects that already have a recorded proposal
ref.
If a proposal reached the target but its ref was not recorded, the rerun sends
the same origin and the target inbox returns the existing proposal instead of
creating a duplicate, as long as the PM checkout's HEAD has not moved. If the
rerun's router output differs from that pending proposal, the command stops
without sending and suggests either recording the existing proposal with
`--link` or withdrawing it with `issuekit discard <id> --to <project>`.

## First request example

Start by confirming the PM checkout can see eligible stored profiles:

```powershell
issuekit profile --all --json
```

Ask for a dry run first. The text output lists only each target's project and
title, so add `--json` to see the proposal bodies:

```powershell
issuekit request --dry-run --json "Add audit logging to the customer export"
```

If the proposed targets and proposal text are right, run the same request
without `--dry-run` to send it. The real run asks the router again, so its
targets or text can differ from the dry run; check the printed result:

```powershell
issuekit request "Add audit logging to the customer export"
```

The command prints a request id. Use it to follow the resulting proposal refs
and their status:

```powershell
issuekit request --status REQUEST_ID
```
