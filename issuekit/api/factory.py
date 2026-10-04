"""Factories and lifecycle helpers for API clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from issuekit.api.client import IssuekitClient
from issuekit.errors import WorkflowError

if TYPE_CHECKING:
    from issuekit.config import IssuekitConfig


def client_for(
    config: IssuekitConfig,
    *,
    project: str | None = None,
    **overrides: Any,
) -> IssuekitClient:
    overrides.setdefault("allow_insecure_api_url", config.allow_insecure_api_url)
    return IssuekitClient(
        config.api_url,
        project=project or config.project,
        timeout=config.api_timeout,
        **overrides,
    )


def require_api_url(
    config: IssuekitConfig,
    what: str,
    *,
    error: type[Exception] = WorkflowError,
) -> str:
    if config.api_url:
        return config.api_url
    message = (
        f"{what} requires api_url. Set api_url in "
        "issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL."
    )
    if error is WorkflowError:
        raise WorkflowError(message, code="missing_api_url")
    raise error(message)


class OwnedApiClient:
    def __init__(
        self,
        config: IssuekitConfig,
        client: IssuekitClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or client_for(config)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
