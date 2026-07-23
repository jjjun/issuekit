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
model = "gpt-5.6"
reasoning_effort = "medium"
```

`agent` defaults to an empty value. It is the only way to select the router
agent because `request` has no `--agent` flag; without it the command fails.
`max_targets` defaults to 3 and limits how many projects one request can
reach. `max_clarify_rounds` defaults to 2. After that many requester answers,
issuekit calls the router with `force_final`. If it still returns `clarify`,
issuekit forcibly converts the decision to a rejection with the reason
`Clarification limit reached and the router still requested clarification.`

`[agents.<name>.roles.router]` supplies the router role's model and reasoning
effort defaults. `--model` and `--reasoning-effort` override those defaults for
one run.

## Keep profiles current

Project profiles are the router's only candidate input. Inspect the local and
stored profile with:

```powershell
issuekit profile
issuekit profile --all
```

A project with no stored profile, or a profile marked stale, cannot receive
routed work. If the router rejects everything, check `issuekit profile --all`
first.

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
  proposal inbox.
- `--target PROJECT` selects which target project's clarification is being
  answered. It is required with `--link`; use it with `--answer` when more than
  one target has a pending question.
- `--link REQUEST_ID` records an existing `project#id` proposal reference for
  an unsent target of a saved route. Use it to recover request state after a
  proposal exists but its reference was not recorded.
- `--json` prints structured output.
- `--dry-run` prints the router decision without sending proposals or saving a
  decision.
- `--timeout-sec SECONDS` sets the router agent's hard timeout (default 600).
- `--model MODEL_ID` and `--reasoning-effort VALUE` override the router agent
  settings for the run.

`--target` is valid only with `--answer` or `--link`. `--link` also requires
both `--target` and the proposal reference as positional `text`.

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
   records the clarification.

`--inbox` is only for replies from target-project triage. It does not show a
pre-routing router question; that question is printed by the original request
and remains associated with its request id.

## First request example

Start by confirming the PM checkout can see eligible stored profiles:

```powershell
issuekit profile --all
```

Ask for a dry run first:

```powershell
issuekit request --dry-run "Add audit logging to the customer export"
```

If the proposed targets and proposal text are right, run the same request
without `--dry-run` to send it:

```powershell
issuekit request "Add audit logging to the customer export"
```

The command prints a request id. Use it to follow the resulting proposal refs
and their status:

```powershell
issuekit request --status REQUEST_ID
```
