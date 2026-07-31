from __future__ import annotations

import json

import httpx
import pytest

from issuekit.api import IssuekitClient
from issuekit.workflow import WorkflowError


def test_agent_session_client_uses_contract_paths_bodies_and_fencing_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/lease/release"):
            return httpx.Response(204)
        if request.url.path.endswith("/commands/claim"):
            return httpx.Response(200, json={"command": None})
        return httpx.Response(200, json={"id": "session-1", "generation": 3})

    client = IssuekitClient(
        "https://mine.example",
        project="demo",
        token="token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    session = client.create_agent_session(
        12,
        {
            "idempotency_key": "create-1",
            "role": "implementer",
            "agent": "codex",
            "runtime": "codex_app_server",
            "worker_id": "demo.worker",
        },
    )
    lease = client.acquire_agent_session_lease(
        12,
        session["id"],
        {
            "worker_id": "demo.worker",
            "acquire_key": "acquire-1",
            "lease_token": "secret",
            "ttl_seconds": 60,
        },
    )
    headers = {
        "X-Issue-Agent-Worker": "demo.worker",
        "X-Issue-Agent-Lease": "secret",
        "X-Issue-Agent-Generation": str(lease["generation"]),
    }
    assert client.claim_agent_command(12, session["id"], headers=headers) == {
        "command": None
    }
    client.release_agent_session_lease(12, session["id"], headers=headers)

    base = "/api/issues/demo/issues/12/agent-sessions"
    assert [request.url.path for request in requests] == [
        base,
        f"{base}/session-1/lease/acquire",
        f"{base}/session-1/commands/claim",
        f"{base}/session-1/lease/release",
    ]
    assert json.loads(requests[0].content)["runtime"] == "codex_app_server"
    assert json.loads(requests[1].content)["lease_token"] == "secret"
    assert json.loads(requests[2].content) == {"max_count": 1}
    assert json.loads(requests[3].content) == {}
    for request in requests[2:]:
        assert request.headers["x-issue-agent-worker"] == "demo.worker"
        assert request.headers["x-issue-agent-lease"] == "secret"
        assert request.headers["x-issue-agent-generation"] == "3"


def test_agent_session_list_uses_opaque_cursor_parameters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"items": [], "next_cursor": None, "has_more": False}
        )

    client = IssuekitClient(
        "https://mine.example",
        project="demo",
        token="token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.list_agent_sessions(
        7, state="sealed", attempt=2, limit=25, cursor="opaque:value"
    )

    assert dict(seen[0].url.params) == {
        "state": "sealed",
        "attempt": "2",
        "limit": "25",
        "cursor": "opaque:value",
    }


def test_agent_session_client_enforces_command_and_event_limits() -> None:
    client = IssuekitClient("https://mine.example", token="token")

    with pytest.raises(WorkflowError) as command_error:
        client.create_agent_command(
            1,
            "session",
            {
                "idempotency_key": "large",
                "kind": "turn_start",
                "payload": {"text": "x" * (32 * 1024 + 1)},
            },
        )
    with pytest.raises(ValueError, match="1 to 100"):
        client.append_agent_events(1, "session", [], headers={})

    assert command_error.value.code == "payload_too_large"
