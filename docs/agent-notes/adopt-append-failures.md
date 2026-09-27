# Adopt append failures and the commit-after-response race

**Applies to:** `issuekit adopt --append-file`, MCP
`adopt_proposal(append=...)`, and any script that loops over adoptions or
reads an issue right after writing it through the mine-py API.

## A failed append leaves a claimable issue without its scope

When the append step fails, `adopt` exits 1, prints `Adopted proposal as
issue #N, but append failed: ...` to stderr, and with `--json` adds an
`append_error` key. By then the proposal is already adopted, and the issue
is in the open implement pool without the appended text. An implementer
that claims it implements the raw proposal.

Scripts must check the exit status of every adopt, or look for
`append_error`. Capturing the output with `out=$(issuekit adopt ... 2>&1)`
and grepping it throws away both signals. That is how eight adoptions in
mine-py lost their scope sections on 2026-09-26 (issuekit#proposal:941).

Recovery: `issuekit edit <id> --append-file <file>`. Add `--force` once the
issue has been claimed. Read the body back with `issuekit show <id> --json`
before handing the issue off.

## The mine-py API commits after responding

`repom.database.get_db_transaction` commits in the exit of a yield
dependency. FastAPI 0.118 and later run the exits of default-scope yield
dependencies after the response has been sent. A read sent right after a
write can therefore miss that write. For a just-created issue, the read
returns 404. This is the likely cause of the append failures above, and it
can affect any read-after-write sequence against that API.
