"""Negotiation thread model and storage backends."""

from __future__ import annotations

from issuekit.config import IssuekitConfig
from issuekit.negotiation.api_store import (
    ApiNegotiationStore,
    _entry_from_api,
    _issue_refs_from_api,
    _thread_summary_from_api,
)
from issuekit.negotiation.mock_store import MockNegotiationStore
from issuekit.negotiation.model import (
    DEFAULT_NEGOTIATION_PATH,
    MAX_CONTRACT_LENGTH,
    NegotiationEntry,
    NegotiationIssueRefs,
    NegotiationStore,
    NegotiationThreadSummary,
    ThreadStatus,
    Verdict,
    _latest_agree_contract,
)
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


__all__ = [
    "ApiNegotiationStore",
    "DEFAULT_NEGOTIATION_PATH",
    "MAX_CONTRACT_LENGTH",
    "MockNegotiationStore",
    "NegotiationEntry",
    "NegotiationIssueRefs",
    "NegotiationStore",
    "NegotiationThreadSummary",
    "ThreadStatus",
    "Verdict",
    "get_negotiation_store",
]
