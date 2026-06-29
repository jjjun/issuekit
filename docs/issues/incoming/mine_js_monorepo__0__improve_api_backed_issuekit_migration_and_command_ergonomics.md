---
origin: mine-js-monorepo#0@37d428b7
to: issuekit
reply_to: 
created: 2026-06-30
title: Improve API-backed issuekit migration and command ergonomics
---

# Proposal: Improve API-backed issuekit migration and command ergonomics

## Summary

During the mine-js-monorepo migration to API-backed issuekit, several CLI edges made agent usage more error-prone. This proposal asks issuekit to improve migration-time guidance and make command outcomes clearer.

## Problems

1. issuekit adopt outcome is ambiguous. In one run, adoption moved the incoming proposal locally, but issuekit info did not show a new active API issue. The agent had to run issuekit author separately. The CLI should make it obvious whether an API issue was created and what id should be used next.

2. issuekit claim is easy to misread as claim <id>. The current command claims the next issue with claim --assignee <agent>. That is fine for the pool model, but agents often need either clearer help text or a specific-issue claim form.

3. Old docs still tell agents to run issuekit generate-indexes. API-backed issuekit does not expose that command, so old instructions fail abruptly during migration.

4. Review commands differ in accepted fields. submit-review takes summary, branch, and commit, while pprove and complete accept verification. Agents can handle this after reading help, but protocol output with concrete examples would reduce mistakes.

## Proposed Solution

- Make issuekit adopt --json and normal output include an explicit API result, for example created_api_issue, issue_id, issue_ref, and 
ext_command.
- If adoption cannot or does not create an API-backed issue, print a clear instruction to run issuekit author from the adopted proposal content.
- Add either issuekit claim --id <id> --assignee <agent> for specific issue claims or update help/protocol text to state that claim always pulls the next eligible issue from the pool.
- Add a compatibility handler for issuekit generate-indexes in API-backed mode that exits cleanly with guidance such as: generate-indexes is not used in API-backed mode; run issuekit validate instead.
- Extend issuekit protocol --agent <agent> with short, copyable CLI examples for author, claim, submit-review, request-changes, approve, complete, incoming, and adopt.

## Impact

- Lower migration friction for repositories moving from markdown issues to API-backed issuekit.
- Fewer agent mistakes caused by stale repo instructions.
- Clearer handoff between proposal adoption and API issue creation.

## Verification

- Run issuekit adopt --json against a sample incoming proposal and confirm the output states whether an API issue was created.
- Run issuekit generate-indexes in API-backed mode and confirm it prints migration guidance without a confusing invalid-command failure.
- Run issuekit protocol --agent codex and verify command examples match current CLI help.
