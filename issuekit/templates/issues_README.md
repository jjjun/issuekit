# Issuekit API Tracker

Issue lifecycle state for this repository is stored in the configured mine-py
API project. The API allocates issue ids and owns claim, review, approval, and
completion transitions.

Configure the API in `pyproject.toml` or `issuekit.toml`:

```toml
[tool.issuekit]
api_url = "https://mine.example"
project = "repo_key"
assignees = ["codex", "claude", "kimi"]
default_reviewer = "auto"
require_distinct_reviewer = true
```

Useful commands:

```powershell
issuekit info
issuekit validate
issuekit author --title "Short title" --body-file issue.md --agent codex
issuekit claim --assignee codex
issuekit submit-review 123 --summary "Implemented."
issuekit approve 123 --verification "uv run pytest"
```

Legacy migration:

```powershell
issuekit migrate-to-api --dry-run
issuekit migrate-to-api
```

Cross-project proposals are still local files under `docs/issues/incoming/`.
They are outside the API issue lifecycle until the proposal flow is migrated.
