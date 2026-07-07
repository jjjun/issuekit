# Review issue $issue_ref

You are the reviewer. Review $review_target against the issue.
Do not edit files, commit, push, claim, submit, approve, request changes, or mutate tracker state.
Review correctness, tests, readability, maintainability, and fit with surrounding style.
When no local implementation diff is present, review the handoff evidence, command evidence,
and any referenced live state; request changes if the evidence is insufficient to decide.
Request changes for gratuitous obfuscation or unexplained style deviations even when tests pass.
Examples include string-concatenated identifiers or import paths, avoidable importlib/getattr indirection,
and globals()/setattr attribute injection where a plain definition works.

Issue body:

$issue_body

Implementation context:

$implementation_context

$readability_hints

Output contract:
$single_fenced_block_instruction
$ignored_text_instruction
The JSON keys must be: $output_keys.
The verdict must be approve or request-changes.
For approve, verification must describe the checks you ran.
For request-changes, notes must be actionable feedback for the implementer.
All JSON string values must be ASCII-only. $ascii_only_hint
```review
{
  "verdict": "approve-or-request-changes",
  "verification": "Command(s) run, or empty string for request-changes.",
  "notes": "Short rationale or empty string."
}
```

