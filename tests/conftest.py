from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


_REPO_LOCAL_CONFIG = Path(__file__).resolve().parents[1] / "issuekit.local.toml"
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


@pytest.fixture(autouse=True)
def preserve_repo_local_config() -> Iterator[None]:
    original = _REPO_LOCAL_CONFIG.read_bytes() if _REPO_LOCAL_CONFIG.exists() else None

    yield

    current = _REPO_LOCAL_CONFIG.read_bytes() if _REPO_LOCAL_CONFIG.exists() else None
    if current == original:
        return
    if original is None:
        _REPO_LOCAL_CONFIG.unlink(missing_ok=True)
    else:
        _REPO_LOCAL_CONFIG.write_bytes(original)
    pytest.fail(
        "test modified the repository's issuekit.local.toml; use tmp_path and "
        "monkeypatch.chdir(tmp_path) for CLI tests",
        pytrace=False,
    )
