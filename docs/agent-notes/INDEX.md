# Agent notes index

One line per note. Add an entry when you add a note; remove it when you delete
one. See [README.md](README.md) for the rules.

- [Waiting on issuekit implement runs](waiting-on-implement-runs.md) - the run
  must be polled in the foreground; background waits do not observe completion.
- [ASCII-only review fields](ascii-only-review-fields.md) - `approve` and
  `submit-review` text fields reject non-ASCII characters.
- [CI policy](ci-policy.md) - which workflows are automatic and which are
  manual-only, and why.
- [One agent maps to one default protocol role](agent-roles-single-default.md) -
  `--agent` returns the wrong protocol when an agent serves two roles; pass
  `--role` instead.
- [MCP test coverage](mcp-test-coverage.md) - the default test environment
  installs MCP; `ISSUEKIT_REQUIRE_MCP=1` makes its absence a hard failure.
