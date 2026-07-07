# Proposal check $check_id for proposal $target_project#$proposal_id

You are checking whether this incoming cross-project proposal should
be accepted by this local repository. Inspect this repository read-only
for feasibility, project scope fit, dependency conflicts, and obvious
implementation risks. Do NOT edit files, run git commit or push, and
do NOT run issuekit claim, submit-review, request-changes, approve,
complete, adopt, discard, or otherwise mutate tracker state.

Target project: $target_project
Proposal id: $proposal_id
Proposal title: $title
Origin: $origin
Blocking: $blocking
Depends-on: $depends_on

Proposal body:

$proposal_body

Verdicts:
- approve: the proposal belongs here, is feasible, and has no blocking
  conflict. It will be automatically adopted after your check.
- revise: the proposal may belong here, but needs concrete changes or
  clarification before it should be adopted.
- reject: the proposal is out of scope, infeasible here, or conflicts
  with this repository's direction.

Output contract:
$single_fenced_block_instruction
$ignored_text_instruction
$ascii_only_rule
Keep comment concise but specific; it is posted to the proposal-check result.
```proposal-check
{
  "verdict": "approve-or-revise-or-reject",
  "comment": "Why this verdict is correct.",
  "spec_markdown": "Optional implementation addendum when verdict is approve."
}
```

