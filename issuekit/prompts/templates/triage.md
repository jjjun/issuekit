# Triage proposal $origin (id $proposal_id)

You are triaging one incoming cross-project proposal for this project.
Inspect this repository read-only to judge whether the request belongs
here and how it should be specified. Do NOT edit files, run git commit or
push, and do NOT run issuekit claim, submit-review, request-changes,
approve, complete, or otherwise mutate tracker or issue lifecycle state.

Proposal title: $title
Origin: $origin
Reply-to: $reply_to
Blocking: $blocking
Depends-on: $depends_on

Proposal body:

$proposal_body

Decide exactly one of:
- adopt: the request belongs to this project. Write an
  implementation-ready spec (background, scope, acceptance criteria,
  affected files) as spec_markdown; it is appended to the adopted issue.
  Include verified or corrected factual claims about this codebase and
  resolved design decisions for any open choices, including implementation
  order when several pending proposals interact.
- adopt_and_reply: adopt the proposal and send reply_markdown to the origin
  only when the origin must take a follow-up action. Do not choose this for a
  proposal that already has a Reply-to value; replies never generate automatic
  replies, which prevents reply loops.
- reply: the request intent is unclear. Ask one concrete question that
  the origin project must answer before this can be adopted.
- discard: the request does not belong to this project. Explain why so
  the sender can re-route.

Output contract:
$single_fenced_block_instruction
$ignored_text_instruction
$ascii_only_rule
```triage
{
  "decision": "adopt-or-adopt_and_reply-or-reply-or-discard",
  "spec_markdown": "Implementation-ready spec when decision is adopt.",
  "reply_markdown": "Required sender follow-up when decision is adopt_and_reply.",
  "question": "One clarifying question when decision is reply.",
  "reason": "Why it does not belong here when decision is discard."
}
```
