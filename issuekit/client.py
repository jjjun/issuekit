"""HTTP client for the issuekit API backend.

The client uses httpx as the transport so tests and later phases can inject a
MockTransport-backed client without requiring a live API server.
"""

from __future__ import annotations

from collections.abc import Mapping
import base64
import json
import os
import time
from typing import Any

import httpx

from issuekit.core import is_valid_workflow_token
from issuekit.workflow import WorkflowError


JsonDict = dict[str, Any]


class IssuekitClient:
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
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_url.strip():
            raise ValueError("api_url is required")
        if not project or not is_valid_workflow_token(project):
            raise ValueError(f"Invalid project token: {project}")
        self.api_url = api_url.rstrip("/")
        self.project = project
        self.timeout = timeout
        self.username = username if username is not None else os.getenv("ISSUEKIT_API_USER")
        self.password = password if password is not None else os.getenv("ISSUEKIT_API_PASSWORD")
        env_token = os.getenv("ISSUEKIT_API_TOKEN")
        self._token = token if token is not None else env_token
        self._token_expiry = _jwt_expiry(self._token)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> "IssuekitClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def login(self, *, force: bool = False) -> str:
        """Log in with service-account credentials and cache the JWT in memory."""
        if not force and self._token and not _is_expired(self._token_expiry):
            return self._token
        if not self.username or not self.password:
            if self._token:
                return self._token
            raise WorkflowError(
                "API credentials are required; set ISSUEKIT_API_USER and "
                "ISSUEKIT_API_PASSWORD or ISSUEKIT_API_TOKEN.",
                code="unauthorized",
            )

        response = self._send(
            "POST",
            "/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Accept": "application/json"},
        )
        payload = self._parse_response(response)
        if not isinstance(payload, dict):
            raise WorkflowError("Login response was not a JSON object.", code="invalid_response")
        token = payload.get("access_token") or payload.get("token")
        if not isinstance(token, str) or not token:
            raise WorkflowError("Login response did not include an access token.", code="invalid_response")
        self._token = token
        self._token_expiry = _response_expiry(payload) or _jwt_expiry(token)
        return token

    def list_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[JsonDict]:
        params = _drop_none(
            {
                "status": status,
                "stage": stage,
                "assignee": assignee,
                "limit": limit,
                "offset": offset,
            }
        )
        payload = self._request("GET", "/", params=params)
        if not isinstance(payload, list):
            raise WorkflowError("List response was not a JSON array.", code="invalid_response")
        return payload

    def get_issue(self, number: int) -> JsonDict:
        payload = self._request("GET", f"/{number}")
        return _ensure_dict(payload, "Issue response")

    def create_issue(self, issue: Mapping[str, Any]) -> JsonDict:
        payload = self._request("POST", "/", json=dict(issue))
        return _ensure_dict(payload, "Create response")

    def claim(self, number: int, *, assignee: str) -> JsonDict:
        payload = self._request("POST", f"/{number}/claim", json={"assignee": assignee})
        return _ensure_dict(payload, "Claim response")

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
    ) -> JsonDict | None:
        payload = self._request(
            "POST",
            "/claim-next",
            json=_drop_none({"assignee": assignee, "priority": priority}),
        )
        if payload is None:
            return None
        return _ensure_dict(payload, "Claim-next response")

    def submit(
        self,
        number: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/submit",
            json=_drop_none(
                {
                    "summary": summary,
                    "branch": branch,
                    "commit": commit,
                    "reviewer": reviewer,
                }
            ),
        )
        return _ensure_dict(payload, "Submit response")

    def request_changes(
        self,
        number: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/request-changes",
            json=_drop_none({"notes": notes, "reviewer": reviewer, "assignee": assignee}),
        )
        return _ensure_dict(payload, "Request-changes response")

    def approve(self, number: int, *, summary: str, verification: str, reviewer: str) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/approve",
            json={
                "summary": summary,
                "verification": verification,
                "reviewer": reviewer,
            },
        )
        return _ensure_dict(payload, "Approve response")

    def complete(self, number: int, *, summary: str, verification: str, force: bool = False) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/complete",
            json={
                "summary": summary,
                "verification": verification,
                "force": force,
            },
        )
        return _ensure_dict(payload, "Complete response")

    def import_issues(self, issues: list[Mapping[str, Any]] | Mapping[str, Any]) -> JsonDict | list[JsonDict]:
        body: Any = [dict(issue) for issue in issues] if isinstance(issues, list) else dict(issues)
        payload = self._request("POST", "/import", json=body)
        if not isinstance(payload, (dict, list)):
            raise WorkflowError("Import response was not JSON data.", code="invalid_response")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        token = self.login()
        response = self._send(
            method,
            self._issue_path(path),
            json=dict(json) if json is not None else None,
            params=dict(params) if params is not None else None,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        if response.status_code == 401:
            token = self.login(force=True)
            response = self._send(
                method,
                self._issue_path(path),
                json=dict(json) if json is not None else None,
                params=dict(params) if params is not None else None,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
        return self._parse_response(response)

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._http.request(method, self._url(path), timeout=self.timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise WorkflowError(f"API request failed: {exc}", code="request_failed") from exc

    def _parse_response(self, response: httpx.Response) -> Any:
        if response.status_code == 204:
            return None
        if 200 <= response.status_code < 300:
            if not response.content:
                return {}
            return response.json()

        code = f"http_{response.status_code}"
        message = response.reason_phrase or f"HTTP {response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            if isinstance(payload.get("code"), str):
                code = payload["code"]
            if isinstance(payload.get("message"), str):
                message = payload["message"]
            elif isinstance(payload.get("detail"), str):
                message = payload["detail"]
        raise WorkflowError(message, code=code)

    def _issue_path(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"/api/issues/{self.project}/issues{suffix}"

    def _url(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.api_url}{suffix}"


def _ensure_dict(payload: Any, label: str) -> JsonDict:
    if not isinstance(payload, dict):
        raise WorkflowError(f"{label} was not a JSON object.", code="invalid_response")
    return payload


def _drop_none(values: Mapping[str, Any]) -> JsonDict:
    return {key: value for key, value in values.items() if value is not None}


def _is_expired(expiry: float | None) -> bool:
    return expiry is not None and expiry <= time.time() + 30


def _response_expiry(payload: Mapping[str, Any]) -> float | None:
    for key in ("expires_at", "expires"):
        raw = payload.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
    raw_expires_in = payload.get("expires_in")
    if isinstance(raw_expires_in, (int, float)):
        return time.time() + float(raw_expires_in)
    return None


def _jwt_expiry(token: str | None) -> float | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    exp = payload.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
