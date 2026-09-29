# Cross-project proposals

Related projects exchange suggestions through API proposal inboxes. Proposals
are not workflow items until they are adopted, so the API-backed issue queue is
separate from proposal triage.

The [PM request router](pm-request.md) is another way to create proposals: it
routes a natural-language request from a dedicated PM checkout to owning
projects.

## Stay in your own project

Use `issuekit author` only for work that originates in and belongs to the
current project. If you are acting in project A and discover that the required
change belongs to project B, stay in project A and send a proposal:

```powershell
issuekit propose --to project-b --title "Short proposal title" --body-file proposal.md
```

Do not `cd` into project B and run `issuekit author`; that bypasses B's proposal
triage and makes the issue look locally originated. When `author` sees related
project context that looks cross-project, it stops before creating the issue and
prints the proposal command template. Pass `--direct-local-author` only when the
work is deliberately local despite mentioning a related project.

If a direct target-project issue was created by mistake, recover without editing
tracker metadata directly: send the proposal from the origin project, then close
the mistaken direct issue in the target project as superseded:

```powershell
issuekit complete <direct-issue-id> --force --summary "Superseded by proposal <proposal-ref>" --verification "Recovery bookkeeping only."
```

## Proposal targets

`--to` takes a registered target API project key, not an arbitrary alias. A
project becomes visible to other repos after that project runs `issuekit add` or
`issuekit register` against the API, or otherwise pushes a project profile.
If the API exposes its project catalog, issuekit rejects unknown targets before
creating a proposal. If the connected API predates project catalog support,
proposal writes continue and issuekit reports that the target could not be
validated.

Do not assume a repo remote name is a valid proposal destination. For example,
if a local alias such as `mine-dashboard` points at an old service name but the
target project is registered as `dashboard`,
`issuekit propose --to mine-dashboard ...` is rejected on catalog-aware APIs
instead of creating a proposal in an unwatched inbox.

## Local aliases (refs)

The old workspace ref registry is kept only as an optional local map of
sibling project names and checkout paths. Proposal delivery does not consult
it: `--to` is used exactly as given and must be a registered API project key.
Refs are read in three places: the `issuekit author` cross-project preflight
(a title or body that names another project's ref makes `author` stop), the
`propose` dependency warning, and the counterpart checkout lookup in
[negotiation](negotiation.md). For a set of sibling repos, place one
`issuekit.workspace.toml` above them:

```toml
[projects]
basekit = "basekit"
fast-domain = "fast-domain"
issuekit = "issuekit"
mine-py = "mine-py"
py_cr_wrapper = "py_cr_wrapper"
repom = "repom"
mine-js-monorepo = "mine-js-monorepo"
infra-toolkit = "infra-toolkit"
```

`issuekit` discovers the nearest `issuekit.workspace.toml` by walking up from
the current directory. `ISSUEKIT_WORKSPACE` can point to an explicit workspace
file and overrides discovery. Relative `[projects]` paths resolve against the
workspace file's directory, so sibling entries like `fast-domain = "fast-domain"`
survive moving the whole workspace. Absolute paths are allowed for out-of-tree
repos.

Each repo can still use gitignored `issuekit.local.toml` with a `[refs]` table
for private refs or overrides. Effective refs are loaded as workspace projects,
then local refs; local entries win on name conflicts.

Manage refs with:

```powershell
issuekit add-ref fast-domain --path C:/abs/path/to/fast-domain
issuekit add-ref fast-domain --path ../fast-domain --scope workspace
issuekit list-refs
```

`add-ref` defaults to `--scope local`. `--scope workspace` writes to the
discovered workspace file; if none is found, create one explicitly or pass
`--path-to-workspace <file>`.

## Sending

```powershell
issuekit propose --to fast-domain --title "Short proposal title" --body-file proposal.md
```

`--from-issue <id>` sends a local issue: its title and body become the
defaults, and its id becomes part of the proposal origin (see
[Origins and de-duplication](#origins-and-de-duplication)). Other flags:

- `--blocking` marks the proposal as a hard dependency of the origin project's
  work. `incoming` and `outgoing` show the flag, and a target with
  `[triage] require_blocking = true` triages only blocking proposals
  automatically.
- `--project <name>` sets the origin project explicitly. Without it, `propose`
  must run inside a local issuekit project (`ISSUEKIT.md`, `issuekit.toml`, or
  `[tool.issuekit]`).
- `--agent <agent>` records the authoring agent in the local STOP guard that
  `propose` writes; see [Separation of duties](separation-of-duties.md).
- `--json` prints the structured result.

If `--to` names the current project and the proposal is not a `--reply`,
issuekit prints a self-target warning, because local work belongs in
`issuekit author`, and still sends the proposal.

For multi-project changes, create or propose the upstream project that owns the
first required API or contract change before sending downstream consumer
proposals. Attach the upstream reference with `--depends-on`:

```powershell
issuekit propose --to mine-js-monorepo --title "Use new API" --body-file proposal.md --depends-on mine-py#proposal:123
```

`--depends-on` can be repeated, and one value can list several refs separated
by commas or spaces; an invalid ref fails the command before anything is sent.
Accepted dependency refs are `project#N`, `project#issue:N`, and
`project#proposal:N`. Bare `project#N` refs are accepted for compatibility, but
can be shadowed when an issue and a proposal share the same number. Prefer
`project#proposal:N` when depending on a not-yet-adopted proposal. Structured
body lines such as `Depends-On: mine-py#proposal:123` are also recognized.
If the proposal body appears to depend on a third project but no upstream
reference is supplied, issuekit prints an advisory warning and still sends the
proposal.

## Origins and de-duplication

Every proposal records an origin of the form `<project>#<id>@<commit>`: the
sending project, the local issue id from `--from-issue` or `--reply`, and the
current short HEAD commit. Without either flag the id is `0`, so every
proposal sent from one commit shares the origin `<project>#0@<HEAD>`.

The target inbox de-duplicates only against its pending proposals with the
same origin. On a match it returns the existing proposal instead of creating
one: the result has `deduplicated: true` and the earlier proposal's id. If the
matched proposal's title, body, or other fields differ from what you sent,
nothing is sent and the command exits 1 with `payload_mismatch: true` and the
differing fields. Pass `--from-issue <id>` so each proposal gets a distinct
origin, or adopt or discard the stale pending proposal. Because the origin
includes the commit, re-sending the same source issue after a new commit
creates a new proposal.

## Triage and reply

```powershell
issuekit incoming
issuekit adopt 42
issuekit discard 43
```

`issuekit discard <id>` discards a pending proposal in the current project's
inbox. To withdraw a pending proposal that the current project sent, name the
target inbox with `--to`:

```powershell
issuekit discard 43 --to fast-domain
```

With `--to`, issuekit refuses a proposal whose origin is another project. The
MCP `discard_proposal` tool takes `to` for the same purpose.

Adoption notes supplied with `issuekit adopt --append-file` are recorded only
on the receiving project's adopted issue. They do not reach the proposal
sender. When [agent triage](#agent-triage) adopts a proposal and the sender
must take a specific follow-up action, it can use `adopt_and_reply` to send
that action back as a linked proposal. This is only for necessary follow-up,
not routine adoption notification. A proposal that is already a reply is
adopted without another automatic reply, preventing reply loops.

Discard decisions remain pull-based: they do not automatically notify the
sender, which can inspect the outcome with `issuekit outgoing --to <project>`.

When appending text, local input errors, including a missing, non-ASCII, or
empty append, fail before adoption and leave the proposal pending. After
adoption, issuekit retries the append on a transient not-found response and
then re-reads the issue to confirm the text; both steps share one retry budget
of about 3.1 seconds. A successful JSON response includes
`append_applied: true` and `appended_chars`. Both failure cases exit 1 and
leave the adopted issue claimable:

- If the append failed, the issue lacks the text; recover with
  `issuekit edit <id> --append-file <file>` or the MCP `update_issue` tool.
- If the append was accepted but the read-back could not confirm it, check
  `issuekit show <id> --json` before appending again, because the text may
  already be there.

To reply after implementing an adopted issue, run:

```powershell
issuekit propose --reply 42 --title "Implemented fast-domain support" --body-file reply.md
```

By default `--reply` derives the destination project from the recorded `origin`
value before `#`. Pass `--to <project>` with `--reply` to override that
destination.

## Agent triage

When `[triage] author_agent` is set, an agent can triage the inbox. Run one
cycle with `issuekit triage --once` (`--once` is currently required), or let
`issuekit serve --triage` run it on each poll. A cycle considers pending
proposals that match the `[triage]` policy: the origin project is listed in
`trusted_origins`, the proposal is blocking when `require_blocking` is set, and
a negotiation has not locked it. It evaluates at most `max_adoptions_per_cycle`
of them. The agent inspects the checkout read-only and returns one decision:

- `adopt`: adopt the proposal at `[triage] default_priority` and append the
  agent's implementation spec to the new issue.
- `adopt_and_reply`: adopt as above, then send the agent's follow-up to the
  origin project as a linked proposal. No reply is sent when the proposal is
  itself a reply.
- `reply`: send one question back to the origin project as a reply proposal
  and leave this proposal pending. Later cycles in the same checkout skip it
  until its content changes.
- `discard`: discard the proposal. The reason is reported locally and is not
  sent to the origin project.

When agent triage adopts a proposal whose body has a line
`Supersedes: <project>#<N>` naming an earlier proposal in the same inbox, it
also discards proposal `<N>` if that proposal is still pending. A line that
names another project, or a malformed line, is ignored. The
[PM request router](pm-request.md) adds this line to amended proposals.

## Directed proposal checks

A proposal check asks a registered worker of the target project to evaluate one
pending proposal. The sender creates it:

```powershell
issuekit proposal-check-request --to fast-domain --proposal 42 --worker <worker.repo[@machine]>
```

Without `--worker`, issuekit picks the target project's only registered worker
and fails with the candidate list when there are several. It warns when the
selected worker is offline or was last seen more than five minutes ago, and it
returns the existing check when that worker already has a pending check for the
proposal. The MCP `create_proposal_check` tool does the same. The sender can
follow each check's status under the proposal in
`issuekit outgoing --to <project>`.

Directed proposal checks are worker-pull based. Run
`issuekit serve --agent <agent> --proposal-checks` from a registered checkout
to poll pending checks addressed to that worker and answer them automatically.
The loop uses `--interval` for idle polling, `--timeout-sec` for each read-only
agent evaluation, `--once` for a single cycle, and `--proposal-check-limit` for
the maximum checks evaluated per cycle. Transient API failures are logged and
retried with the same capped backoff used by the issue and review serve loops.

For a single pass, run `issuekit proposal-checks --once` in the worker
checkout; `issuekit proposal-checks --list` shows the checks addressed to it
without running an agent. The agent inspects the checkout read-only and
returns a comment with one verdict:

- `approve`: the proposal belongs here and is feasible. The worker adopts it
  automatically, unless it is already adopted, at `[triage] default_priority`,
  appends the agent's optional spec, and records the adopted issue ref on the
  check.
- `revise`: the proposal needs concrete changes or clarification first.
- `reject`: the proposal is out of scope or infeasible here.

`revise` and `reject` post only the verdict and comment; issuekit does not
adopt or discard the proposal.

See [Directed addressing](directed-addressing.md) for how directed targets are
resolved.
