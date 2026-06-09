---
origin: mine-js-monorepo#0@3cd65ca0
to: issuekit
reply_to: 
created: 2026-06-09
title: Enforce codex guardrails: mojibake gate + minimal-additive diffs when driving GPT-5.3-Codex-Spark
---

# Proposal: Enforce codex guardrails: mojibake gate + minimal-additive diffs when driving GPT-5.3-Codex-Spark

# Proposal: enforce codex guardrails (mojibake gate + minimal-additive diffs) when driving GPT-5.3-Codex-Spark

## Context

This proposal comes from a real session in `mine-js-monorepo` where issuekit drove
the configured `codex` agent (GPT-5.3-Codex-Spark 5) through issues #513, #514, and
#515 via `issuekit implement <id> --agent codex`. The functional output was good
(tests passed, TypeScript/ESLint clean), but the agent exhibited two repeatable,
high-cost failure modes that the reviewer had to catch by hand and that issuekit is
well positioned to prevent at the point where it launches the agent.

The goal of this proposal: when issuekit drives codex, inject guardrail
instructions and/or a pre-submit gate so these behaviors are blocked or
auto-detected before `submit_for_review`, rather than relying on the human/AI
reviewer to notice them every time.

## Observed failure mode 1: mojibake on existing non-ASCII content (hard to detect)

When codex edited a file that already contained non-ASCII text (Japanese doc
comments in `apps/mine-dashboard/lib/aiClient.ts`), it re-encoded the existing
multibyte characters into garbage bytes (mojibake). Example: an existing comment
written in Japanese (a "503/429 retry" note) was turned into a run of garbled
CJK-looking bytes after the edit, while the surrounding ASCII code was unchanged.

Why this is dangerous:
- It silently corrupts pre-existing, untouched content.
- The repo's encoding gate runs `issuekit check-encoding --no-mojibake`, i.e. with
  mojibake detection DISABLED, so the existing CI gate does not catch it. Only a
  byte-level / manual read caught it.
- It survived one round of `request_changes`: when asked to fix it, codex replaced
  the corrupted Japanese with rewritten English rather than restoring the original,
  and still left the rest of the file churned.

## Observed failure mode 2: wholesale reformatting / scope creep instead of additive diffs

The issue explicitly said: "Add `callAiChatMessages` ALONGSIDE `callAiChat`; do not
change `callAiChat`." Codex instead reformatted the entire file: flipped every
single-quote to double-quote, deleted JSDoc blocks, translated/removed existing
comments, reworded error strings, and added trailing commas on untouched lines. The
diff for what should have been a purely additive change was +123/-42.

This violated the issue's own "no unnecessary changes / no reformatting of lines the
change does not need" acceptance gate. It also did not self-correct: after an
explicit `request_changes` saying "reset the file to HEAD and re-apply ONLY the new
functions in the existing style," codex submitted a second time still fully
reformatted (+124/-43). The reviewer ultimately had to reset the file to HEAD and
re-apply the additive code directly to land it (final diff: +108/-0).

## Suggested enforcement (issuekit side)

When `issuekit implement --agent codex` (or the configured codex agent profile)
launches the agent, consider any of:

1. Mojibake gate on by default for codex runs. Before accepting/submitting, run an
   encoding check WITH mojibake detection (i.e. do not pass `--no-mojibake`, or run
   a dedicated mojibake pass) over the agent's touched files, and block
   `submit_for_review` if it trips. At minimum, surface a warning in the run output.

2. Diff-shape self-check. After the agent finishes, compute the per-file diff vs the
   base ref and flag files where deletions on pre-existing lines are large relative
   to the net additive intent (e.g. a file the issue said to "add to" comes back
   with heavy `-` churn). This catches the "reformatted the whole file" pattern.

3. Standing instruction injected into the codex system/launch prompt, e.g.:
   - "Make minimal, additive diffs. Do not reformat, re-quote, re-order imports, or
     rewrite/translate comments on lines unrelated to your change."
   - "Never alter existing non-ASCII (e.g. Japanese) text. Preserve existing
     comments byte-for-byte unless the task is specifically to change them. After
     editing, verify you introduced no mojibake."
   - "When a task says 'add X alongside Y, do not change Y,' the diff must touch only
     the added region; if you cannot, stop and report instead of reformatting."

4. Optional: on `request_changes`, pass the prior reviewer notes back into the codex
   prompt verbatim and add "address ONLY these notes; do not re-touch unrelated
   lines," since the agent twice ignored a scoped change request.

## Why this belongs in issuekit

issuekit owns the launch path for the codex agent and already owns the
`check-encoding` tooling. Centralizing these guardrails there means every repo that
delegates to GPT-5.3-Codex-Spark via issuekit benefits, without each repo having to
re-encode the same CLAUDE.md warnings and without depending on a human/AI reviewer
to catch the same two issues on every run.

## Evidence pointers (in mine-js-monorepo)

- Failure mode 1 & 2: `apps/mine-dashboard/lib/aiClient.ts` across issue #514's two
  codex submissions (mojibake on lines that were originally Japanese; +123/-42 then
  +124/-43 reformatting churn). Resolved by reviewer reset-to-HEAD + additive
  re-apply (+108/-0).
- The repo already documents the BOM/CRLF half of this in CLAUDE.md ("codex keeps
  adding UTF-8 BOM/CRLF") and gates BOM via `check:errors`; mojibake is the gap.
