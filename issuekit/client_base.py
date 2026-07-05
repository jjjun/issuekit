"""Shared transport and response helpers for the issuekit API client."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import httpx

from issuekit.client_security import (
    _is_expired,
    _jwt_expiry,
    _response_expiry,
    _warn_insecure_api_url,
)
from issuekit.core import _drop_none
from issuekit.token_cache import (
    _cached_token_miss_message,
    _delete_cached_token,
    _write_cached_token,
)
from issuekit.workflow import WorkflowError


JsonDict = dict[str, Any]
JsonBody = Mapping[str, Any] | Sequence[Mapping[str, Any]]
_LOGIN_GUIDANCE = (
    "API credentials are required; run `issuekit login` or set "
    "ISSUEKIT_API_USER and ISSUEKIT_API_PASSWORD or ISSUEKIT_API_TOKEN."
)


class _ClientTransportMixin:
    api_url: str
    project: str
    timeout: float
    username: str | None
    password: str | None
    _external_token: bool
    _token: str | None
    _token_expiry: float | None
    _http: httpx.Client

    def login(self, *, force: bool = False) -> str:
        """Log in with service-account credentials and cache the JWT."""
        if not force and self._token and not _is_expired(self._token_expiry):
            return self._token
        if not self.username or not self.password:
            if self._external_token and self._token and not _is_expired(self._token_expiry):
                return self._token
            raise WorkflowError(_login_guidance(self.api_url), code="unauthorized")

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

    def health(self) -> JsonDict:
        """Read the unauthenticated backend health payload."""
        response = self._send("GET", "/health", headers={"Accept": "application/json"})
        payload = self._parse_response(response)
        return _ensure_dict(payload, "Health response")

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
                raise WorkflowError(_login_guidance(self.api_url), code="unauthorized")
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

    def _paginate(
        self,
        path: str,
        *,
        collection: str | None,
        params: Mapping[str, Any],
        page_label: str,
        item_label: str,
        page_size: int,
    ) -> Iterator[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        page_size = min(page_size, 500)
        offset = 0
        while True:
            page_params = _drop_none({**params, "limit": page_size, "offset": offset})
            if collection is None:
                payload = self._authorized_request("GET", path, params=page_params)
            else:
                payload = self._request("GET", path, collection=collection, params=page_params)
            page = _ensure_dict(payload, page_label)
            items = page.get("items")
            if not isinstance(items, list):
                raise WorkflowError(
                    f"{page_label} items was not a JSON array.",
                    code="invalid_response",
                )
            for item in items:
                yield _ensure_dict(item, item_label)

            total = page.get("total")
            limit = page.get("limit", page_size)
            current_offset = page.get("offset", offset)
            if (
                not isinstance(total, int)
                or not isinstance(limit, int)
                or not isinstance(current_offset, int)
                or limit <= 0
            ):
                raise WorkflowError(
                    f"{page_label} pagination fields were invalid.",
                    code="invalid_response",
                )
            if current_offset + len(items) >= total or len(items) < limit:
                return
            offset = current_offset + limit

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
            try:
                return response.json()
            except ValueError as exc:
                raise WorkflowError(
                    "API response was not valid JSON.",
                    code="invalid_response",
                ) from exc

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


def _login_guidance(api_url: str) -> str:
    miss_message = _cached_token_miss_message(api_url)
    if miss_message is None:
        return _LOGIN_GUIDANCE
    return f"{_LOGIN_GUIDANCE} {miss_message}."


def _items_envelope_rows(payload: Any, *, page_label: str, item_label: str) -> list[JsonDict]:
    page = _ensure_dict(payload, page_label)
    items = page.get("items")
    if not isinstance(items, list):
        raise WorkflowError(
            f"{page_label} items was not a JSON array.",
            code="invalid_response",
        )
    return [_ensure_dict(item, item_label) for item in items]


def _array_or_items_rows(payload: Any, *, page_label: str, item_label: str) -> list[JsonDict]:
    if isinstance(payload, list):
        return [_ensure_dict(item, item_label) for item in payload]
    return _items_envelope_rows(payload, page_label=page_label, item_label=item_label)


def _profile_rows(payload: Any) -> list[JsonDict]:
    # Accept a bare JSON array or a paginated {"items": [...]} envelope so the
    # client tolerates either backend list shape.
    return _array_or_items_rows(
        payload,
        page_label="Project profile list response",
        item_label="Project profile response",
    )


def _worker_rows(payload: Any) -> list[JsonDict]:
    # Accept either a bare JSON array or a paginated {"items": [...]} envelope so
    # the client tolerates either backend list shape.
    return _array_or_items_rows(
        payload,
        page_label="Worker list response",
        item_label="Worker response",
    )
