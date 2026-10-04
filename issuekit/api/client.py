"""HTTP client for the issuekit API backend.

The client uses httpx as the transport so tests and later phases can inject a
MockTransport-backed client without requiring a live API server. Injected
clients should be configured with follow_redirects=True to match the default
client created here; auth requests disable redirects per request.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Mapping
from functools import cache
from threading import Lock

import httpx

from issuekit.core import is_valid_workflow_token

from .agent_sessions import AgentSessionResourceMixin
from .base import ClientTransportMixin
from .issue_resources import IssueResourceMixin
from .profile_resources import ProfileResourceMixin
from .proposal_check_resources import ProposalCheckResourceMixin
from .proposal_resources import ProposalResourceMixin
from .security import jwt_expiry
from .token_cache import read_cached_token
from .worker_resources import WorkerResourceMixin

# Keep idle sockets disabled for long-lived clients (issuekit#179).
DEFAULT_HTTP_LIMITS = httpx.Limits(
    max_connections=5,
    max_keepalive_connections=0,
    keepalive_expiry=1.0,
)
# Use only when a client closes promptly or polls more often than expiry (issuekit#179).
BURST_HTTP_LIMITS = httpx.Limits(
    max_connections=5,
    max_keepalive_connections=2,
    keepalive_expiry=5.0,
)


@cache
def _shared_ssl_context() -> ssl.SSLContext:
    return httpx.create_ssl_context()


class IssuekitClient(
    AgentSessionResourceMixin,
    IssueResourceMixin,
    WorkerResourceMixin,
    ProfileResourceMixin,
    ProposalResourceMixin,
    ProposalCheckResourceMixin,
    ClientTransportMixin,
):
    """Synchronous client for mine-py's issuekit-compatible API."""

    def __init__(
        self,
        api_url: str,
        *,
        project: str = "issuekit",
        timeout: float = 30.0,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        use_env_token: bool = True,
        allow_insecure_api_url: bool = False,
        http_client: httpx.Client | None = None,
        http_limits: httpx.Limits | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not api_url.strip():
            raise ValueError("api_url is required")
        if not project or not is_valid_workflow_token(project):
            raise ValueError(f"Invalid project token: {project}")
        self.api_url = api_url.rstrip("/")
        self.project = project
        self.timeout = timeout
        self.allow_insecure_api_url = allow_insecure_api_url
        self.username = username if username is not None else os.getenv("ISSUEKIT_API_USER")
        self.password = password if password is not None else os.getenv("ISSUEKIT_API_PASSWORD")
        env_token = os.getenv("ISSUEKIT_API_TOKEN") if use_env_token else None
        self._external_token = token is not None or env_token is not None
        self._token = token if token is not None else env_token
        self._token_expiry = jwt_expiry(self._token)
        self._login_lock = Lock()
        if self._token is None:
            cached = read_cached_token(self.api_url)
            if cached is not None:
                self._token = cached["token"]
                self._token_expiry = cached["expires_at"]
        self._owns_http_client = http_client is None
        if http_client is None:
            self._http = httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                limits=http_limits or DEFAULT_HTTP_LIMITS,
                verify=_shared_ssl_context(),
                headers=headers,
            )
        else:
            self._http = http_client

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    @property
    def token_expiry(self) -> float | None:
        return self._token_expiry

    def __enter__(self) -> IssuekitClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
