from __future__ import annotations

import pytest


_ISSUEKIT_ENV_KEYS = (
    "ISSUEKIT_API_PASSWORD",
    "ISSUEKIT_API_TIMEOUT",
    "ISSUEKIT_API_TOKEN",
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_USER",
    "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
    "ISSUEKIT_PROJECT",
    "ISSUEKIT_SESSION",
    "ISSUEKIT_TOKEN_CACHE",
)


@pytest.fixture(autouse=True)
def isolated_issuekit_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live_contract"):
        return

    for key in _ISSUEKIT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ISSUEKIT_CONFIG", "")
