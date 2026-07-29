# Testing

Run the normal project gates by hand before publishing changes:

```powershell
uv run ruff check
uv run pytest
uv run issuekit check-encoding
uv run issuekit check-encoding --gate
```

Ruff is the local lint gate. Run `uv run ruff check --fix` to apply its safe
automatic fixes before resolving any remaining findings deliberately.

The default encoding check scans complete tracked source files for BOM, likely
mojibake, stray carriage returns, and CRLF. The `--gate` mode separately
reproduces the submit gate for the current worktree, including its changed-line
scope and unconfirmed mojibake failures.

Run the full suite, including MCP tests, with `uv run --with mcp pytest`.

Pytest uses concise progress output by default while retaining failure details
and the final test summary. For verbose progress during interactive diagnosis,
run `uv run pytest -o addopts= -v`.

## Live contract tests

The default pytest suite is intended for offline development and CI. Tests that
would call a live issuekit API backend are marked `live_contract` and skip
unless explicitly enabled.

Run only the live contract checks with a reachable delete-safe test backend:

```powershell
$env:ISSUEKIT_RUN_LIVE_CONTRACTS = "1"
$env:ISSUEKIT_API_URL = "https://mine.example"
uv run pytest -m live_contract
```

`ISSUEKIT_RUN_LIVE_CONTRACTS=1` is the opt-in switch. `ISSUEKIT_API_URL` must
point at the backend under test; individual future live contract tests may also
use the normal API credential variables, such as `ISSUEKIT_API_TOKEN` or
`ISSUEKIT_API_USER` and `ISSUEKIT_API_PASSWORD`, when they hit authenticated
endpoints.

## CI

Maintainers can also run the same pytest and encoding checks from GitHub
Actions with the manual `Tests` workflow using the `Run workflow` button.
