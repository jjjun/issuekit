# Proposal origins are per source issue and commit

**Applies to:** `issuekit propose` and pending proposals in a target inbox

An origin is `<project>#<issue|0>@<commit>`: the source project, the issue id
from `--from-issue` or `--reply` (or `0` when neither is supplied), and the
current commit. Proposals from the same source issue and commit share an
origin; it is not distinct per proposal.

The target de-duplicates only pending proposals with the same origin. A
matching payload returns the existing id with `deduplicated: true`. If the
payload differs, the command exits 1 with `payload_mismatch: true`; compared
fields are `title`, `body`, `reply_to`, `blocking`, `depends_on`, and
`target_worker`. Check both flags before treating a propose call as a new
proposal.
