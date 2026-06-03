---
id: 26
status: completed
priority: medium
created: 2026-06-03
completed: 2026-06-03
stage: done
title: Shared workspace ref registry for cross-project proposals
---

# Issue #26: Shared workspace ref registry for cross-project proposals

## Problem

Issue #25 added cross-project proposals with a machine-local ref registry
(`issuekit.local.toml`, a `[refs]` table mapping a short name to an absolute
repository path). This works for one or two related repos, but does not scale to
a whole workspace of sibling projects.

In the current environment there are 8 related repos under a single parent
directory (basekit, fast-domain, issuekit, mine-py, py_cr_wrapper, repom,
mine-js-monorepo, infra-toolkit). To let any repo propose to any other and to
support `propose --reply` everywhere, each repo would need to register the other
seven by absolute path. That is N*(N-1) = 56 duplicated absolute-path entries
spread across 8 gitignored files, and every entry breaks if the workspace is
moved or cloned to a different machine.

The per-repo absolute-path model also forces the reply convention (ref name must
equal the sender's directory name) to be re-applied by hand in every file, which
is the same coupling flagged as Minor 1 in issue #25.

## Proposed Solution

Add a single shared workspace registry that lists all related projects once, with
paths relative to the registry file, and have every repo resolve refs against it.
Keep the per-repo `issuekit.local.toml` working as a local override/addition so
existing setups and one-off refs keep functioning.

Resolution becomes a two-layer merge:

1. Discover the nearest `issuekit.workspace.toml` by walking up from the current
   working directory (like git/pyproject discovery); stop at the filesystem root.
   An `ISSUEKIT_WORKSPACE` environment variable overrides discovery with an
   explicit file path (useful for CI and tests).
2. Load its `[projects]` table (name -> path). Relative paths resolve against the
   workspace file's own directory, so a workspace of siblings uses bare directory
   names and survives being moved. Absolute paths are allowed for out-of-tree
   repos.
3. Overlay the per-repo `issuekit.local.toml` `[refs]` table. Local entries win
   on name conflicts, so a repo can override a path or add a private ref without
   editing the shared file.

Example shared file at the workspace root (one file for all 8 repos):

```toml
# issuekit.workspace.toml
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

Ref name equals the target directory name (all 8 names already match
`WORKFLOW_TOKEN_PATTERN`), so both `propose --to <name>` and the auto-derived
`propose --reply` destination resolve uniformly with no per-repo bookkeeping.

This is fully backward compatible: with no workspace file present, resolution
falls back to the existing per-repo `issuekit.local.toml` behavior unchanged.

## Impact

- Modified: `issuekit/refs.py` (workspace discovery, `[projects]` loader,
  relative-path base resolution, merge with local `[refs]`, resolution-source
  tracking)
- Modified: `issuekit/commands/propose.py` (`list-refs` shows each ref source and
  resolved absolute path; `add-ref` gains `--scope local|workspace`)
- Modified: `issuekit/cli.py` (register `add-ref --scope`)
- Modified: `README.md`, `docs/issues/README.md`,
  `issuekit/templates/issues_README.md` (document the workspace registry,
  discovery, precedence, and the ref-name == directory-name convention)
- New/Modified tests: `tests/test_refs.py`, `tests/test_cli.py`

No change to proposal files, issue schema, validate, indexes, or the claim/review
workflow. Refs remain an inert local address book; this only changes how a ref
name resolves to a path.

## Implementation Plan

1. Workspace discovery and loading (`issuekit/refs.py`):
   - `WORKSPACE_CONFIG_NAME = "issuekit.workspace.toml"`.
   - `find_workspace_file(cwd)`: honor `ISSUEKIT_WORKSPACE` if set (must exist),
     else walk parents from `cwd` for the nearest workspace file; return None if
     not found.
   - `load_workspace_refs(cwd)`: parse `[projects]` into name -> resolved path,
     where each value is `Path(value)` joined to the workspace file directory if
     relative, then normalized. Validate each name with the existing
     `_validate_ref_name`. Raise `RefError` on a malformed table.

2. Merge layer (`issuekit/refs.py`):
   - Change the internal resolver so the effective ref map is
     `{**workspace_refs, **local_refs}` (local wins). Keep `load_refs` (local
     only) for backward compatibility and add `load_effective_refs(cwd)` that
     returns name -> (path, source) where source is `"workspace"` or `"local"`.
   - `resolve_ref(name, cwd)` uses the merged map; on unknown name raise the same
     `Unknown ref` error. Keep loading the target repo's own `IssuekitConfig` for
     `issues_dir`.
   - `list_refs`/a new `effective_refs` returns the merged, source-tagged view.

3. CLI (`issuekit/commands/propose.py`, `issuekit/cli.py`):
   - `add-ref` gains `--scope {local,workspace}` (default `local` to preserve
     current behavior). `--scope workspace` writes to the discovered workspace
     file, or errors with guidance if none is found (do not silently create one
     outside any repo unless `--path-to-workspace` is given; keep it explicit).
   - `list-refs` prints `name  source  resolved-path`, marking the current repo's
     own entry as `self` when the resolved path equals the repo root.

4. Self handling:
   - The current repo may appear in the shared registry. `resolve_ref` of self is
     allowed but pointless; `list-refs` marks it `self`. `propose --to <self>` is
     not special-cased (writing a proposal into your own `incoming/` is harmless).
   - Optional: when building an outbound origin, prefer the workspace-registered
     name for the current repo if present, else fall back to `default_repo_ref`
     (directory name). With the name == directory-name convention these are equal;
     this only adds robustness if they ever differ.

5. Docs: explain discovery order (`ISSUEKIT_WORKSPACE` > nearest workspace file >
   per-repo local), relative-path base, local-overrides-workspace precedence, and
   the ref-name == directory-name convention required for `--reply`. ASCII only.

## Test Plan

- `uv run pytest tests/test_refs.py tests/test_cli.py`
- Discovery: a workspace file two directories above `cwd` is found; none above
  yields fallback to local-only; `ISSUEKIT_WORKSPACE` overrides discovery.
- Relative paths: `[projects]` entries resolve against the workspace file's
  directory, not `cwd`; moving the workspace dir (simulated via tmp_path) keeps
  resolution correct.
- Merge precedence: a name present in both the workspace file and
  `issuekit.local.toml` resolves to the local path; a local-only name still
  resolves; a workspace-only name resolves.
- Unknown ref still raises `Unknown ref`.
- `resolve_ref` returns the target repo's configured `issues_dir`.
- propose/reply integration: with two fake sibling repos registered only in the
  shared workspace file, `propose --to B` writes into B's `incoming/`, and after
  adopt -> claim -> complete in B, `propose --reply <id>` resolves the origin ref
  back to A purely via the workspace registry (no per-repo local.toml).
- CLI: `list-refs` shows source and resolved path and marks `self`;
  `add-ref --scope workspace` writes the shared file entry; default
  `add-ref` still writes `issuekit.local.toml`.
- Backward compatibility: existing issue #25 ref tests pass unchanged when no
  workspace file exists.
- Run full `uv run pytest`, `uv run issuekit validate`, and
  `uv run issuekit check-encoding`.

## Related Resources

- `issuekit/refs.py` (`load_refs` L28, `resolve_ref` L68, `default_repo_ref` L81,
  `_validate_ref_name` L86)
- `issuekit/commands/propose.py` (`run_add_ref` L27, `run_list_refs` L37,
  `build_proposal` reply destination L129)
- `issuekit/core.py` (`WORKFLOW_TOKEN_PATTERN` L15, `is_valid_workflow_token`
  L270)
- Issue #25 (cross-project proposal exchange; Minor 1 ref-name coupling this
  resolves)

## Handoff

- Summary: Implemented shared workspace refs with workspace discovery, local override precedence, source-aware list-refs, workspace add-ref scope, docs, and tests.
- Branch: `main`
- Commit: `c551ecded490098adc2b5a3f38d8507a04920404`

**Completed**: 2026-06-03

## Completion Notes

- Approved by claude.
- Verification: `uv run pytest (161 passed, 18 skipped); issuekit validate (26 files, 0 warnings); check-encoding clean. Verified end-to-end across two sibling repos resolved only via a shared issuekit.workspace.toml: propose --to, adopt -> claim -> complete, and propose --reply all resolve through the workspace registry. Confirmed ref names may differ from directory names (alpha/beta vs dirA/dirB) and reply still resolves, because current_repo_ref maps the repo path back to its registered name; this fully removes the #25 Minor 1 name coupling. Backward compatible: no workspace file falls back to per-repo local refs (existing #25 ref tests pass).`
