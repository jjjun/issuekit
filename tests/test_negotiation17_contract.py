from __future__ import annotations

import os

import httpx
import pytest


# Delete-safe live E2E artifact for negotiation thread 17 / issuekit#126 / mine-py#393.
_RUN_LIVE_CONTRACTS = "ISSUEKIT_RUN_LIVE_CONTRACTS"
_ISSUEKIT_API_URL = "ISSUEKIT_API_URL"


@pytest.mark.live_contract
def test_negotiation17_finalize_recovery_contract() -> None:
    if os.environ.get(_RUN_LIVE_CONTRACTS) != "1":
        pytest.skip(
            f"{_RUN_LIVE_CONTRACTS}=1 is not set; skipping live negotiation 17 contract check."
        )

    api_url = os.environ.get(_ISSUEKIT_API_URL)
    if not api_url:
        pytest.skip(
            f"{_ISSUEKIT_API_URL} is not set; skipping live negotiation 17 contract check."
        )

    url = f"{api_url.rstrip('/')}/e2e/ping"
    try:
        response = httpx.get(url, timeout=5.0)
    except httpx.RequestError as exc:
        pytest.skip(f"Live negotiation 17 backend is unreachable at {url}: {exc}")

    assert response.status_code == 200, (
        f"Live negotiation 17 contract expected HTTP 200 from {url}, "
        f"got {response.status_code}: {response.text}"
    )
    assert response.headers.get("content-type", "").startswith("application/json"), (
        f"Live negotiation 17 contract expected JSON response from {url}, "
        f"got {response.headers.get('content-type')!r}."
    )
    assert response.json() == {"ok": True}
