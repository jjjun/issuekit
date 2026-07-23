# ASCII-only review fields

**Applies to:** `issuekit approve`, `issuekit complete`, `issuekit submit-review`

The `--verification` and `--summary` text passed to these commands must be
plain ASCII. Non-ASCII content fails the call, including characters that are
easy to introduce without noticing:

- em dash (`-` is safe, the long dash is not)
- curly quotes pasted from a chat client or editor
- Japanese text of any kind

Write these fields in English ASCII from the start rather than drafting in
another language and translating after a rejection.
