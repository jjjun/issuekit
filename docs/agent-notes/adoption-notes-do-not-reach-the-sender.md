# Adoption notes never reach the proposal sender

**Applies to:** `issuekit adopt`, adopted issues, and proposal replies

`issuekit adopt <id> --append-file <notes>` appends notes to the adopted issue
in the receiving project; they do not reach the origin project. To send a
linked response to the sender, use `issuekit propose --reply <id>`. That reply
derives its destination from the adopted issue's origin unless `--to` is
supplied.

`issuekit edit --body-file` replaces the whole issue body;
`issuekit edit --append-file` preserves the adoption notes. Use the append form
when adding text to an adopted issue. For proposals from the same source issue
and commit, see [proposal-origin-dedup.md](proposal-origin-dedup.md).
