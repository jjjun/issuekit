# ASCII-only review fields

**Applies to:** `issuekit approve`, `issuekit complete`,
`issuekit submit-review`, `issuekit request-changes`, and the matching MCP tools

These text fields must be plain ASCII:

- `approve` and `complete`: `--summary` and `--verification`
- `submit-review`: `--summary`, `--branch`, and `--commit`. It has no
  `--verification`. When `--branch` is omitted, the current git branch name is
  checked instead, so a non-ASCII branch name also fails the submit.
- `request-changes`: `--notes`

The `--*-file` variants are checked the same way. Non-ASCII content fails the
call, including characters that are easy to introduce without noticing:

- em dash (`-` is safe, the long dash is not)
- curly quotes pasted from a chat client or editor
- Japanese text of any kind

Write these fields in English ASCII from the start rather than drafting in
another language and translating after a rejection.
