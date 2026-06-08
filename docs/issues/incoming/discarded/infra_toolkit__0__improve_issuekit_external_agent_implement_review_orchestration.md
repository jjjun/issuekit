---
origin: infra-toolkit#0@10762a8
to: issuekit
reply_to: 
created: 2026-06-08
title: Improve issuekit external-agent implement/review orchestration
---

# Proposal: Improve issuekit external-agent implement/review orchestration

## Context

While coordinating infra-toolkit issue #120, the `issuekit implement 120 --agent kimi` flow worked end to end:

- The issue was authored and assigned to `kimi`.
- `issuekit implement 120 --agent kimi --timeout-sec 900` launched the external implementer.
- The implementer completed the code changes and submitted the issue to review.
- The reviewer re-ran verification and completed the issue with `issuekit complete 120`.

The workflow is useful, but several friction points appeared that would be worth improving in issuekit itself.

## Observed Friction

1. `issuekit implement` is not obvious from the protocol text.

   The implementer protocol emphasizes `claim_next_task` and `submit_for_review`. The CLI command that launches a configured external agent, `issuekit implement <id> --agent <agent>`, was discovered only by checking `issuekit implement --help`.

2. Implement runs can finish without a commit.

   The current protocol says implementers should make focused commits, but the run returned unstaged code changes and still moved the issue into review. This can leave issuekit state completed while Git state is still uncommitted.

3. `.agent-runs/` remains as an untracked working-tree artifact.

   These logs are useful for review, but the expected handling is not obvious. Codex needs clear guidance on whether to ignore, delete, archive, or stage these files.

4. Reviewer approval is implicit.

   In practice, the reviewer used `issuekit complete <id>` to approve a review-stage issue. This works, but the mapping from "approve review" to `complete` is not obvious. An alias such as `issuekit approve` or clearer reviewer protocol text would help.

5. Agent run logs may contain mojibake.

   The implementation stdout log contained mojibake in user-facing summary text. This did not corrupt issue files, but it made review logs noisier.

## Proposed Improvements

1. Update protocol output to document external-agent orchestration:

   - Author creates an issue.
   - Orchestrator may run `issuekit implement <id> --agent <agent> --timeout-sec <seconds>`.
   - The implement command is expected to claim or operate on the assigned issue, run the configured agent, and submit for review.
   - Reviewer then verifies and completes or requests changes.

2. Add reviewer protocol guidance:

   - For a review-stage issue, run verification, inspect the diff and agent logs, then use `issuekit complete <id> --summary ... --verification ...` to approve.
   - Use `issuekit request-changes <id> --notes ...` when the implementation is incomplete.

3. Add a post-implement warning when Git changes are uncommitted:

   - If `issuekit implement` exits successfully but no commit is present, print a clear warning.
   - Include the current `git status --short`.
   - Optionally record whether the implementation commit is missing in the status JSON.

4. Document `.agent-runs/` handling:

   - State whether it should usually be ignored, deleted after review, or kept out of commits.
   - Consider adding it to the generated `.gitignore` unless the intended behavior is to keep logs tracked.

5. Consider adding `issuekit approve` as a reviewer-friendly alias:

   - Equivalent to `issuekit complete` for review-stage issues.
   - Could reject non-review-stage issues unless `--force` is supplied.

6. Investigate stdout/stderr encoding for agent runs on Windows:

   - Ensure logs are written as UTF-8.
   - If external agents emit undecodable bytes, normalize or document the limitation.

## Suggested Codex Skill

This could also be supported by a Codex skill, but issuekit-owned protocol text should still be the source of truth. A Codex skill would simply follow issuekit commands:

1. Check `issuekit queue --assignee <agent>`.
2. Run `issuekit implement <id> --agent <agent> --timeout-sec ...`.
3. Read `.agent-runs/*.status.json` and log tails.
4. Inspect `git diff`.
5. Re-run required verification.
6. Use `issuekit request-changes` or `issuekit complete`.
7. Report final issue state and uncommitted Git state.

## Related Source Context

- infra-toolkit issue #120: `docs/issues/completed/120_fix_host_tui_left_pane_border_and_keyboard_navigation.md`
- Run command used: `issuekit implement 120 --agent kimi --timeout-sec 900`
- Completion command used: `issuekit complete 120 --summary ... --verification ...`
