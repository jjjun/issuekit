"""Negotiation store selection."""

from __future__ import annotations

from issuekit.config import IssuekitConfig
from issuekit.negotiation.api_store import ApiNegotiationStore
from issuekit.negotiation.mock_store import MockNegotiationStore
from issuekit.negotiation.model import NegotiationStore
from issuekit.workflow import WorkflowError


def get_negotiation_store(
    config: IssuekitConfig,
    *,
    use_mock: bool,
) -> NegotiationStore:
    if use_mock:
        return MockNegotiationStore()
    if not config.api_url:
        raise WorkflowError(
            "API negotiation store requires api_url. Set api_url in "
            "issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL.",
            code="missing_api_url",
        )
    return ApiNegotiationStore(config)
