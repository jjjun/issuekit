from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from issuekit.client import IssuekitClient
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


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
        assert request.url.path == "/api/issues/demo_project/issues/"
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
