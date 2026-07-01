"""HTTP client for the issuekit API backend.

The client uses httpx as the transport so tests and later phases can inject a
MockTransport-backed client without requiring a live API server. Injected
clients should be configured with follow_redirects=True to match the default
client created here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from typing import Any

import httpx

from issuekit.client_security import (
    _is_expired,
    _jwt_expiry,
    _response_expiry,
    _warn_insecure_api_url,
)
from issuekit.core import _drop_none, is_valid_workflow_token
from issuekit.token_cache import _delete_cached_token, _read_cached_token, _write_cached_token
from issuekit.workflow import WorkflowError


JsonDict = dict[str, Any]
JsonBody = Mapping[str, Any] | Sequence[Mapping[str, Any]]
_LOGIN_GUIDANCE = (
    "API credentials are required; run `issuekit login` or set "
    "ISSUEKIT_API_USER and ISSUEKIT_API_PASSWORD or ISSUEKIT_API_TOKEN."
)


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
        use_env_token: bool = True,
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
        env_token = os.getenv("ISSUEKIT_API_TOKEN") if use_env_token else None
        self._external_token = token is not None or env_token is not None
        self._token = token if token is not None else env_token
        self._token_expiry = _jwt_expiry(self._token)
        if self._token is None:
            cached = _read_cached_token(self.api_url)
            if cached is not None:
                self._token = cached["token"]
                self._token_expiry = cached["expires_at"]
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    @property
    def token_expiry(self) -> float | None:
        return self._token_expiry

    def __enter__(self) -> "IssuekitClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def login(self, *, force: bool = False) -> str:
        """Log in with service-account credentials and cache the JWT."""
        if not force and self._token and not _is_expired(self._token_expiry):
            return self._token
        if not self.username or not self.password:
            if self._external_token and self._token and not _is_expired(self._token_expiry):
                return self._token
            raise WorkflowError(_LOGIN_GUIDANCE, code="unauthorized")

        _warn_insecure_api_url(self.api_url)
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
        if not self._external_token:
            _write_cached_token(self.api_url, token, self._token_expiry)
        return token

    def logout(self) -> None:
        """Best-effort API logout followed by local token-cache removal."""
        token = self._token
        if token and not _is_expired(self._token_expiry):
            try:
                response = self._send(
                    "POST",
                    "/auth/logout",
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                self._parse_response(response)
            except WorkflowError:
                pass
        _delete_cached_token(self.api_url)
        self._token = None
        self._token_expiry = None

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

    def list_all_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        page_size = min(page_size, 500)
        offset = 0
        issues: list[JsonDict] = []
        while True:
            batch = self.list_issues(
                status=status,
                stage=stage,
                assignee=assignee,
                limit=page_size,
                offset=offset,
            )
            issues.extend(batch)
            if len(batch) < page_size:
                return issues
            offset += page_size

    def get_issue(self, number: int) -> JsonDict:
        payload = self._request("GET", f"/{number}")
        return _ensure_dict(payload, "Issue response")

    def create_issue(self, issue: Mapping[str, Any]) -> JsonDict:
        payload = self._request("POST", "/", json=dict(issue))
        return _ensure_dict(payload, "Create response")

    def claim(self, number: int, *, assignee: str, worker: str | None = None) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/claim",
            json=_drop_none({"assignee": assignee, "worker": worker}),
        )
        return _ensure_dict(payload, "Claim response")

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
        worker: str | None = None,
    ) -> JsonDict | None:
        payload = self._request(
            "POST",
            "/claim-next",
            json=_drop_none({"assignee": assignee, "priority": priority, "worker": worker}),
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
        worker: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/request-changes",
            json=_drop_none(
                {"notes": notes, "reviewer": reviewer, "assignee": assignee, "worker": worker}
            ),
        )
        return _ensure_dict(payload, "Request-changes response")

    def approve(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
        worker: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/approve",
            json=_drop_none(
                {
                    "summary": summary,
                    "verification": verification,
                    "reviewer": reviewer,
                    "worker": worker,
                }
            ),
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
        items = [dict(issue) for issue in issues] if isinstance(issues, list) else [dict(issues)]
        payload = self._request("POST", "/import", json={"issues": items})
        if not isinstance(payload, (dict, list)):
            raise WorkflowError("Import response was not JSON data.", code="invalid_response")
        return payload

    def upsert_worker(
        self,
        *,
        machine_id: str,
        repo_id: str,
        worker_id: str,
        path: str | None,
    ) -> JsonDict:
        payload = self._authorized_request(
            "POST",
            "/api/workers",
            json={
                "machine_id": machine_id,
                "repo_id": repo_id,
                "worker_id": worker_id,
                "path": path,
            },
        )
        return _ensure_dict(payload, "Worker response")

    def create_proposal(
        self,
        *,
        origin: str,
        title: str,
        body: str,
        reply_to: str | None = None,
        priority: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            "/",
            collection="proposals",
            json=_drop_none(
                {
                    "origin": origin,
                    "title": title,
                    "body": body,
                    "reply_to": reply_to,
                    "priority": priority,
                }
            ),
        )
        return _ensure_dict(payload, "Proposal response")

    def list_proposals(
        self,
        *,
        status: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        page_size = min(page_size, 500)
        offset = 0
        proposals: list[JsonDict] = []
        while True:
            payload = self._request(
                "GET",
                "/",
                collection="proposals",
                params=_drop_none({"status": status, "limit": page_size, "offset": offset}),
            )
            page = _ensure_dict(payload, "Proposal list response")
            items = page.get("items")
            if not isinstance(items, list):
                raise WorkflowError(
                    "Proposal list response items was not a JSON array.",
                    code="invalid_response",
                )
            proposals.extend(_ensure_dict(item, "Proposal response") for item in items)

            total = page.get("total")
            limit = page.get("limit", page_size)
            current_offset = page.get("offset", offset)
            if not isinstance(total, int) or not isinstance(limit, int) or not isinstance(current_offset, int):
                raise WorkflowError(
                    "Proposal list response pagination fields were invalid.",
                    code="invalid_response",
                )
            if current_offset + len(items) >= total or len(items) < limit:
                return proposals
            offset = current_offset + limit

    def get_proposal(self, proposal_id: int) -> JsonDict:
        payload = self._request("GET", f"/{proposal_id}", collection="proposals")
        return _ensure_dict(payload, "Proposal response")

    def adopt_proposal(self, proposal_id: int, *, priority: str | None = None) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{proposal_id}/adopt",
            collection="proposals",
            json=_drop_none({"priority": priority}),
        )
        return _ensure_dict(payload, "Adopt proposal response")

    def discard_proposal(self, proposal_id: int) -> JsonDict:
        payload = self._request("POST", f"/{proposal_id}/discard", collection="proposals")
        return _ensure_dict(payload, "Discard proposal response")

    def import_proposals(self, proposals: list[Mapping[str, Any]] | Mapping[str, Any]) -> list[JsonDict]:
        items = [dict(proposal) for proposal in proposals] if isinstance(proposals, list) else [dict(proposals)]
        payload = self._request("POST", "/import", collection="proposals", json={"proposals": items})
        if not isinstance(payload, list):
            raise WorkflowError("Proposal import response was not a JSON array.", code="invalid_response")
        return [_ensure_dict(item, "Proposal import response item") for item in payload]

    def _request(
        self,
        method: str,
        path: str,
        *,
        collection: str = "issues",
        json: JsonBody | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        return self._authorized_request(
            method,
            self._collection_path(collection, path),
            json=json,
            params=params,
        )

    def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        json: JsonBody | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        _warn_insecure_api_url(self.api_url)
        token = self.login()
        response = self._send(
            method,
            path,
            json=json,
            params=dict(params) if params is not None else None,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        if response.status_code == 401:
            if not self.username or not self.password:
                raise WorkflowError(_LOGIN_GUIDANCE, code="unauthorized")
            token = self.login(force=True)
            response = self._send(
                method,
                path,
                json=json,
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

    def _collection_path(self, collection: str, path: str) -> str:
        if path in ("", "/"):
            suffix = ""
        elif path.startswith("/"):
            suffix = path
        else:
            suffix = f"/{path}"
        return f"/api/issues/{self.project}/{collection}{suffix}"

    def _url(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.api_url}{suffix}"


def _ensure_dict(payload: Any, label: str) -> JsonDict:
    if not isinstance(payload, dict):
        raise WorkflowError(f"{label} was not a JSON object.", code="invalid_response")
    return payload
