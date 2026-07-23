# CI policy

**Applies to:** `.github/workflows/`

This repo deliberately keeps code tests off the automatic path:

- `dependency-audit.yml` runs on a weekly schedule. This is the only
  automatic workflow.
- `tests.yml` (pytest and `issuekit check-encoding`) is `workflow_dispatch`
  only. It is never a push or pull-request gate.

Do not add a push or pull-request trigger to the test workflow. Verification
belongs to the implementer and reviewer running the gates locally, as described
in [../guides/testing.md](../guides/testing.md). If an issue is filed asking to
make the tests an automatic gate, close it rather than implementing it.
