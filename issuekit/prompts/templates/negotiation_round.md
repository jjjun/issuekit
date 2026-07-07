You are participating in an issuekit cross-repo design negotiation.
Perspective: you represent the $side side.
Round job: propose, counter, agree, or block the current contract.

Seed:
$seed

Resolved contract so far:
$resolved_contract

Compact thread so far:
$thread_summary

Read budget:
$negotiation_read_budget
Do not read or include whole-repo dumps.

Output contract:
$single_fenced_block_instruction
$ignored_text_instruction
The JSON keys must be: $output_keys.
The verdict must be one of: $verdict_values.
The contract value must be a string or null.
The notes value must be short free text.
```negotiation
{
  "side": "$side",
  "verdict": "propose",
  "contract": "Small proposed contract text, or null",
  "notes": "Short rationale."
}
```

