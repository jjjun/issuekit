---
id: 27
status: in_progress
priority: medium
created: 2026-06-03
completed: 
assignee: codex
stage: implementing
implementer: codex
origin: mine-js-monorepo#0@f8b6c5b3
title: Reviewer-mismatch error wording and propose git-call timeout hardening
---

# Issue #27: Reviewer-mismatch error wording and propose git-call timeout hardening

## Problem

Adopted from an incoming cross-project proposal.

## Proposed Solution

# Proposal: Reviewer-mismatch error wording and propose git-call timeout hardening

Two independent issues observed while driving a review handoff from a consumer
repo (mine-js-monorepo) via the issuekit MCP server.

## 1. Confusing reviewer-mismatch error in request_changes / approve

### Context
Reviewing an issue at stage=review (implementer=codex, reviewer driven by
default_reviewer). I called `request_changes(id, notes, reviewer="claude")`,
passing the reviewer explicitly to be safe.

### Problem
The tool returned:

    "Issue #453 is assigned to codex, not claude."

This conflates two concepts: the message talks about the issue *assignee* (the
implementer, codex), but the argument I passed was `reviewer`. It does not name
the expected reviewer, nor state the remediation. I had to call `get_protocol`
to learn the correct usage is to omit `reviewer` so it defaults to
`default_reviewer`. Calling `request_changes(id, notes)` with no reviewer then
worked.

### Proposed change
When an explicit `reviewer` argument does not match the issue's assigned
reviewer at stage=review, return a message that:
- names the expected reviewer (the assigned reviewer / how default_reviewer
  resolved), distinct from the implementer assignee, and
- states the remediation: omit `reviewer` to use default_reviewer, or pass the
  assigned reviewer value.

Example:
    "Issue #453 review is assigned to reviewer '<resolved>'. You passed
    reviewer='claude'. Omit `reviewer` to use default_reviewer, or pass the
    assigned reviewer."

Secondary: the `--notes` ASCII-only rule is enforced only as a post-submit hard
error ("--notes must be ASCII-only"). Surfacing this requirement directly in the
`get_protocol` step that introduces request_changes / approve / submit_for_review
would make it visible at the point of use.

## 2. propose hangs if the git rev-parse call blocks (no timeout)

### Context
`build_proposal` computes the origin commit via `_git_commit`, which runs git
without a timeout:

    issuekit/commands/propose.py, _git_commit():
    subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd,
                   check=True, capture_output=True, text=True)

### Problem
There is no `timeout=` argument. If git ever blocks (index.lock contention, a
hook, a credential helper, or a slow/locked filesystem), `propose` hangs
indefinitely with no recovery path, even though the function already has a
fallback (`return "unknown"`) for git failures.

This is a latent robustness issue, not a frequent one: in my repro `git
rev-parse --short HEAD` returned in ~64ms with no lock files, so it was not the
trigger of the hang I saw (that was an unrelated host-side gate). But the
missing timeout means a blocked git would take propose down with it.

### Proposed change
Add a bounded timeout and treat a timeout like the existing failure path:

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, check=True, capture_output=True, text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"

(`subprocess.SubprocessError` covers both `CalledProcessError` and
`TimeoutExpired`.) Consider the same treatment for any other unbounded
`subprocess.run` git calls in the codebase.

## Impact
Item 1 is error-message / protocol-text wording only; no review state-machine
change. Item 2 is a small defensive change in `_git_commit`. Both reduce
operator round trips and avoid indefinite hangs.

## Local triage notes

Both items were verified against the current code on 2026-06-03:

- Item 1: the conflated message lives in `issuekit/workflow.py:180` (the
  `request_changes` reviewer check) and the parallel assignee check in
  `issuekit/workflow.py:144` (`submit_for_review`). At `default_reviewer = auto`
  the issue stays assigned to the implementer at stage=review, so an explicit
  `reviewer` mismatch produces "assigned to <implementer>, not <reviewer>",
  which conflates assignee and reviewer exactly as reported.
- Item 2: `issuekit/commands/propose.py:198` (`_git_commit`) already has a
  `try/except (OSError, CalledProcessError)` with a `return "unknown"` fallback
  but no `timeout=`, confirming the latent hang. It is the only `subprocess.run`
  git call in `issuekit/`. `current_repo_ref` (`issuekit/refs.py:175`) is
  file-based and does not shell out. `issuekit/commands/check_encoding.py:115`
  and `:120` use `subprocess.check_output` for `git ls-files` without a timeout;
  treat hardening those as optional, lower-priority follow-up.

## Implementation Plan

1. Item 2 (bug-leaning, do first): in `issuekit/commands/propose.py`
   `_git_commit`, add `timeout=5` to the `subprocess.run` call and broaden the
   `except` to `(OSError, subprocess.SubprocessError)` so `TimeoutExpired` and
   `CalledProcessError` both fall through to `return "unknown"`.
2. Item 1 (wording): in `issuekit/workflow.py`, when an explicit `reviewer`
   does not match the resolved assigned reviewer at stage=review, raise a
   message that names the expected reviewer (and how `default_reviewer`
   resolved) distinct from the implementer assignee, and states the remediation
   (omit `reviewer` to use `default_reviewer`, or pass the assigned reviewer).
   Apply the same clarity to the `submit_for_review` assignee mismatch at
   `workflow.py:144` only if it can be done without changing behavior.
3. Item 1 secondary (docs): surface the `--notes` ASCII-only requirement in the
   `get_protocol` step that introduces `request_changes` / `approve` /
   `submit_for_review`, so it is visible at the point of use rather than only as
   a post-submit hard error. Update the relevant text in `issuekit/protocol.py`.
4. Optional follow-up (out of strict scope): add timeouts to the
   `git ls-files` calls in `check_encoding.py` if trivial; otherwise leave a
   note and skip.

## Test Plan

- Add/adjust a unit test for `_git_commit` covering the timeout path (mock
  `subprocess.run` to raise `TimeoutExpired`, assert it returns `"unknown"`).
- Add/adjust a workflow test asserting the new reviewer-mismatch message names
  the expected reviewer and the remediation text.
- Run the existing test suite (`uv run pytest` or the repo's configured runner)
  and `uv run issuekit check-encoding`.

## Related Resources

- Origin: `mine-js-monorepo#0@f8b6c5b3`
