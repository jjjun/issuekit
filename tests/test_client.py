from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from issuekit.client import IssuekitClient
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


@pytest.fixture(autouse=True)
def isolated_token_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(tmp_path / "token.json"))


def test_client_logs_in_once_and_sends_expected_request_shape() -> None:
    login_count = 0
    seen_api_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/auth/login":
            login_count += 1
            assert "authorization" not in request.headers
            assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
            form = parse_qs(request.content.decode("utf-8"))
            assert form == {"username": ["svc"], "password": ["secret"]}
            return httpx.Response(200, json={"access_token": _jwt(exp=time.time() + 3600)})

        seen_api_requests.append(request)
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.url.path == "/api/issues/demo_project/issues"
        if request.method == "POST":
            assert json.loads(request.content) == {"title": "First"}
            return httpx.Response(201, json={"id": 1, "title": "First"})
        assert request.method == "GET"
        assert dict(request.url.params) == {"stage": "review", "limit": "5"}
        return httpx.Response(200, json=[{"id": 1, "stage": "review"}])

    client = IssuekitClient(
        "https://mine.example/",
        project="demo_project",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.create_issue({"title": "First"}) == {"id": 1, "title": "First"}
    assert client.list_issues(stage="review", limit=5) == [{"id": 1, "stage": "review"}]

    assert login_count == 1
    assert [request.method for request in seen_api_requests] == ["POST", "GET"]


def test_client_list_issues_uses_collection_path_without_trailing_slash() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json=[])

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_issues() == []

    assert len(seen_requests) == 1
    assert seen_requests[0].method == "GET"
    assert seen_requests[0].url.path == "/api/issues/demo_project/issues"


def test_client_create_issue_uses_collection_path_without_trailing_slash() -> None:
    issue = {"title": "First"}
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert json.loads(request.content) == issue
        return httpx.Response(201, json={"id": 1, **issue})

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.create_issue(issue) == {"id": 1, **issue}

    assert len(seen_requests) == 1
    assert seen_requests[0].method == "POST"
    assert seen_requests[0].url.path == "/api/issues/demo_project/issues"


def test_client_owned_http_client_follows_redirects() -> None:
    client = IssuekitClient("https://mine.example", token="static-token")
    try:
        assert client._http.follow_redirects is True
    finally:
        client.close()


def test_client_reauthenticates_once_after_401() -> None:
    login_count = 0
    issue_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, issue_count
        if request.url.path == "/auth/login":
            login_count += 1
            return httpx.Response(
                200,
                json={"access_token": _jwt(exp=time.time() + 3600, subject=f"token-{login_count}")},
            )

        issue_count += 1
        if issue_count == 1:
            assert "token-1" in _decode_payload(request.headers["authorization"].split(" ", 1)[1])
            return httpx.Response(401, json={"code": "unauthorized", "message": "expired"})
        assert "token-2" in _decode_payload(request.headers["authorization"].split(" ", 1)[1])
        return httpx.Response(200, json={"id": 7})

    client = IssuekitClient(
        "https://mine.example",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.get_issue(7) == {"id": 7}
    assert login_count == 2
    assert issue_count == 2


def test_login_writes_token_cache_with_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    expires_at = time.time() + 3600

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/login"
        return httpx.Response(200, json={"access_token": "cached-token", "expires_at": expires_at})

    client = IssuekitClient(
        "https://mine.example/",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.login() == "cached-token"

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload == {
        "https://mine.example": {
            "token": "cached-token",
            "expires_at": expires_at,
        }
    }
    if os.name != "nt":
        assert cache_path.stat().st_mode & 0o777 == 0o600


def test_second_client_reuses_cached_token_without_login(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_PASSWORD", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_TOKEN", raising=False)
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/auth/login":
            login_count += 1
            return httpx.Response(
                200,
                json={"access_token": _jwt(exp=time.time() + 3600, subject="cached")},
            )
        assert "cached" in _decode_payload(request.headers["authorization"].split(" ", 1)[1])
        return httpx.Response(200, json=[])

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    first = IssuekitClient(
        "https://mine.example",
        username="svc",
        password="secret",
        http_client=http_client,
    )
    first.login()

    second = IssuekitClient("https://mine.example", http_client=http_client)

    assert second.list_issues() == []
    assert login_count == 1


def test_expired_cached_token_triggers_relogin_and_refreshes_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    cache_path.write_text(
        json.dumps(
            {
                "https://mine.example": {
                    "token": _jwt(exp=time.time() - 3600, subject="old"),
                    "expires_at": time.time() - 3600,
                }
            }
        ),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/login"
        return httpx.Response(
            200,
            json={"access_token": _jwt(exp=time.time() + 3600, subject="new")},
        )

    client = IssuekitClient(
        "https://mine.example",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    token = client.login()

    assert "new" in _decode_payload(token)
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "new" in _decode_payload(payload["https://mine.example"]["token"])


def test_no_cache_and_no_credentials_names_login_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(tmp_path / "token.json"))
    monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_PASSWORD", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_TOKEN", raising=False)
    client = IssuekitClient(
        "https://mine.example",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )

    with pytest.raises(WorkflowError, match="issuekit login"):
        client.login()


def test_logout_removes_cache_entry_and_calls_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_PASSWORD", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_TOKEN", raising=False)
    token = _jwt(exp=time.time() + 3600)
    cache_path.write_text(
        json.dumps({"https://mine.example": {"token": token, "expires_at": time.time() + 3600}}),
        encoding="utf-8",
    )
    logout_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logout_count
        assert request.url.path == "/auth/logout"
        assert request.headers["authorization"] == f"Bearer {token}"
        logout_count += 1
        return httpx.Response(204)

    client = IssuekitClient(
        "https://mine.example",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.logout()

    assert logout_count == 1
    assert not cache_path.exists()


def test_cache_is_keyed_by_api_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_PASSWORD", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_TOKEN", raising=False)
    cache_path.write_text(
        json.dumps(
            {
                "https://mine.example": {
                    "token": _jwt(exp=time.time() + 3600),
                    "expires_at": time.time() + 3600,
                }
            }
        ),
        encoding="utf-8",
    )
    client = IssuekitClient(
        "https://other.example",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )

    with pytest.raises(WorkflowError, match="issuekit login"):
        client.login()


def test_issuekit_api_token_is_not_written_to_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    monkeypatch.setenv("ISSUEKIT_API_TOKEN", "externally-managed-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer externally-managed-token"
        return httpx.Response(200, json=[])

    client = IssuekitClient(
        "https://mine.example",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_issues() == []
    assert not cache_path.exists()


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(404, "not_found"), (422, "invalid_project"), (409, "invalid_transition")],
)
def test_client_maps_server_errors(status_code: int, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"code": code, "message": f"{code} happened"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(WorkflowError) as excinfo:
        client.get_issue(99)

    assert str(excinfo.value) == f"{code} happened"
    assert excinfo.value.code == code


def test_client_claim_next_204_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/claim-next"
        assert json.loads(request.content) == {"assignee": "codex"}
        return httpx.Response(204)

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim_next(assignee="codex") is None


def test_client_import_issues_posts_wrapped_issues_body() -> None:
    items = [
        {
            "id": 73,
            "title": "Fix API import",
            "status": "in_progress",
            "priority": "high",
        }
    ]
    imported = [{"id": 73, "title": "Fix API import", "stage": "done"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/issuekit/issues/import"
        assert json.loads(request.content) == {"issues": items}
        return httpx.Response(200, json=imported)

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.import_issues(items) == imported


def test_client_request_passes_list_json_body_without_dict_coercion() -> None:
    body = [
        {
            "id": 73,
            "status": "in_progress",
            "priority": "high",
            "created": "2026-06-29",
            "completed": None,
            "assignee": "codex",
            "stage": "implementing",
            "implementer": "codex",
            "author": "claude",
            "title": "Fix API import",
            "body": "Issue body",
            "labels": ["api"],
            "comments": [],
            "metadata": {"source": "test"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/import"
        assert json.loads(request.content) == body
        return httpx.Response(200, json=[])

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client._request("POST", "/import", json=body) == []


def test_client_create_issue_still_posts_dict_body() -> None:
    issue = {"title": "First", "priority": "high"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues"
        assert json.loads(request.content) == issue
        return httpx.Response(201, json={"id": 1, **issue})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.create_issue(issue) == {"id": 1, **issue}


def test_client_submit_sends_exact_server_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/7/submit"
        assert json.loads(request.content) == {
            "summary": "implemented",
            "branch": "main",
            "commit": "abc123",
            "reviewer": "claude",
        }
        return httpx.Response(200, json={"id": 7, "stage": "review"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.submit(
        7,
        summary="implemented",
        branch="main",
        commit="abc123",
        reviewer="claude",
    ) == {"id": 7, "stage": "review"}


def test_client_approve_sends_exact_server_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/7/approve"
        assert json.loads(request.content) == {
            "summary": "approved",
            "verification": "uv run python -m pytest",
            "reviewer": "claude",
        }
        return httpx.Response(200, json={"id": 7, "stage": "done"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.approve(
        7,
        summary="approved",
        verification="uv run python -m pytest",
        reviewer="claude",
    ) == {"id": 7, "stage": "done"}


def test_client_complete_sends_exact_server_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/7/complete"
        assert json.loads(request.content) == {
            "summary": "completed",
            "verification": "manual",
            "force": True,
        }
        return httpx.Response(200, json={"id": 7, "stage": "done"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.complete(7, summary="completed", verification="manual", force=True) == {
        "id": 7,
        "stage": "done",
    }


def test_fake_issuekit_client_round_trips_create_list_get_claim() -> None:
    client = FakeIssuekitClient()

    created = client.create_issue({"title": "First", "priority": "high"})
    listed = client.list_issues()
    fetched = client.get_issue(created["id"])
    claimed = client.claim(created["id"], assignee="codex")

    assert created["id"] == 1
    assert listed == [created]
    assert fetched == created
    assert claimed["assignee"] == "codex"
    assert claimed["stage"] == "implementing"
    assert claimed["implementer"] == "codex"
    assert client.claim_next(assignee="codex") is None


def _jwt(*, exp: float, subject: str = "svc") -> str:
    header = _b64({"alg": "none", "typ": "JWT"})
    payload = _b64({"sub": subject, "exp": exp})
    return f"{header}.{payload}.signature"


def _b64(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_payload(token: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
