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

The old workspace ref registry is kept only as an optional local alias map for
operators who want to remember sibling project names. Workspace/local refs can
remain convenient aliases, but proposal delivery resolves to registered API
project keys and no longer resolves a target repo path or writes files. For a
set of sibling repos, place one `issuekit.workspace.toml` above them:

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

For multi-project changes, create or propose the upstream project that owns the
first required API or contract change before sending downstream consumer
proposals. Attach the upstream reference with `--depends-on`:

```powershell
issuekit propose --to mine-js-monorepo --title "Use new API" --body-file proposal.md --depends-on mine-py#proposal:123
```

Accepted dependency refs are `project#N`, `project#issue:N`, and
`project#proposal:N`. Bare `project#N` refs are accepted for compatibility, but
can be shadowed when an issue and a proposal share the same number. Prefer
`project#proposal:N` when depending on a not-yet-adopted proposal. Structured
body lines such as `Depends-On: mine-py#proposal:123` are also recognized.
If the proposal body appears to depend on a third project but no upstream
reference is supplied, issuekit prints an advisory warning and still sends the
proposal.

## Triage and reply

```powershell
issuekit incoming
issuekit adopt 42
issuekit discard 43
```

Adoption notes supplied with `issuekit adopt --append-file` are recorded only
on the receiving project's adopted issue. They do not reach the proposal
sender. When automated triage adopts a proposal and the sender must take a
specific follow-up action, it can use `adopt_and_reply` to send that action
back as a linked proposal. This is only for necessary follow-up, not routine
adoption notification. A proposal that is already a reply is adopted without
another automatic reply, preventing reply loops.
Discard decisions remain pull-based: they do not automatically notify the
sender, which can inspect the outcome with `issuekit outgoing --to <project>`.

To reply after implementing an adopted issue, run:

```powershell
issuekit propose --reply 42 --title "Implemented fast-domain support" --body-file reply.md
```

By default `--reply` derives the destination project from the recorded `origin`
value before `#`. Pass `--to <project>` with `--reply` to override that
destination.

Proposal de-duplication is keyed by the full `origin`, including `@commit`.
Re-sending the same source issue after a new commit creates a new proposal.

## Directed proposal checks

Directed proposal checks are worker-pull based. Run
`issuekit serve --agent <agent> --proposal-checks` from a registered checkout
to poll pending checks addressed to that worker and answer them automatically.
The loop uses `--interval` for idle polling, `--timeout-sec` for each read-only
agent evaluation, `--once` for a single cycle, and `--proposal-check-limit` for
the maximum checks evaluated per cycle. Transient API failures are logged and
retried with the same capped backoff used by the issue and review serve loops.

See [Directed addressing](directed-addressing.md) for how directed targets are
resolved.
