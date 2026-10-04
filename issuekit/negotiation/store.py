"""Negotiation store selection."""

from __future__ import annotations

from issuekit.api.factory import require_api_url
from issuekit.config import IssuekitConfig
from issuekit.negotiation.api_store import ApiNegotiationStore
from issuekit.negotiation.mock_store import MockNegotiationStore
from issuekit.negotiation.model import NegotiationStore


def get_negotiation_store(
    config: IssuekitConfig,
    *,
    use_mock: bool,
) -> NegotiationStore:
    if use_mock:
        return MockNegotiationStore()
    require_api_url(config, "API negotiation store")
    return ApiNegotiationStore(config)
