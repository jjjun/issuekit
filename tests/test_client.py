from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import issuekit.client_security as security_module
import issuekit.token_cache as token_cache_module
from issuekit.client import IssuekitClient
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


@pytest.fixture(autouse=True)
def isolated_token_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(tmp_path / "token.json"))
    monkeypatch.delenv("ISSUEKIT_ALLOW_INSECURE", raising=False)
    security_module._WARNED_INSECURE_API_URLS.clear()
    token_cache_module._WARNED_LOOSE_TOKEN_CACHE_PATHS.clear()


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


def test_client_health_gets_unauthenticated_top_level_endpoint() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"status": "ok", "migration_revision": "abc123"})

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.health() == {"status": "ok", "migration_revision": "abc123"}
    assert len(seen_requests) == 1
    assert seen_requests[0].method == "GET"
    assert seen_requests[0].url.path == "/health"


def test_client_health_requires_json_object_response() -> None:
    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )

    with pytest.raises(WorkflowError) as excinfo:
        client.health()

    assert excinfo.value.code == "invalid_response"
    assert str(excinfo.value) == "Health response was not a JSON object."


def test_client_list_all_issues_paginates_until_empty_final_page() -> None:
    all_issues = [{"id": issue_id} for issue_id in range(1, 7)]
    seen_pages: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params["limit"])
        offset = int(params["offset"])
        seen_pages.append((limit, offset))
        return httpx.Response(200, json=all_issues[offset : offset + limit])

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_all_issues(page_size=3) == all_issues
    assert seen_pages == [(3, 0), (3, 3), (3, 6)]


def test_client_list_all_issues_caps_page_size_at_server_max() -> None:
    all_issues = [{"id": issue_id} for issue_id in range(1, 502)]
    seen_pages: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params["limit"])
        offset = int(params["offset"])
        seen_pages.append((limit, offset))
        return httpx.Response(200, json=all_issues[offset : offset + limit])

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_all_issues(page_size=999) == all_issues
    assert seen_pages == [(500, 0), (500, 500)]


def test_client_list_all_issues_can_include_completed_via_board_endpoint() -> None:
    all_issues = [{"id": issue_id} for issue_id in range(1, 4)]
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        params = dict(request.url.params)
        assert request.url.path == "/api/issues/board"
        assert params["projects"] == "demo_project"
        assert params["include_completed"] == "true"
        limit = int(params["limit"])
        offset = int(params["offset"])
        return httpx.Response(
            200,
            json={
                "items": all_issues[offset : offset + limit],
                "total": len(all_issues),
                "limit": limit,
                "offset": offset,
            },
        )

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_all_issues(include_completed=True, page_size=2) == all_issues
    assert [dict(request.url.params) for request in seen_requests] == [
        {
            "projects": "demo_project",
            "include_completed": "true",
            "limit": "2",
            "offset": "0",
        },
        {
            "projects": "demo_project",
            "include_completed": "true",
            "limit": "2",
            "offset": "2",
        },
    ]


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


def test_client_update_issue_uses_issue_resource_path_and_editable_payload() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert json.loads(request.content) == {
            "title": "Updated title",
            "body": "Updated body",
            "priority": "high",
        }
        return httpx.Response(
            200,
            json={
                "id": 7,
                "title": "Updated title",
                "body": "Updated body",
                "priority": "high",
            },
        )

    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.update_issue(
        7,
        {
            "title": "Updated title",
            "body": "Updated body",
            "priority": "high",
            "ignored": "value",
        },
    ) == {
        "id": 7,
        "title": "Updated title",
        "body": "Updated body",
        "priority": "high",
    }

    assert len(seen_requests) == 1
    assert seen_requests[0].method == "PATCH"
    assert seen_requests[0].url.path == "/api/issues/demo_project/issues/7"


def test_client_update_issue_rejects_empty_update() -> None:
    client = IssuekitClient(
        "https://mine.example",
        project="demo_project",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )

    with pytest.raises(ValueError, match="at least one editable field"):
        client.update_issue(7, {"title": None, "body": None, "priority": None})


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not used on Windows")
def test_token_cache_directory_is_created_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "nested" / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))

    token_cache_module._write_token_cache({"https://mine.example": {"token": "cached-token"}})

    assert cache_path.parent.stat().st_mode & 0o777 == 0o700
    assert cache_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not used on Windows")
def test_token_cache_read_warns_once_when_file_is_group_or_other_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ISSUEKIT_TOKEN_CACHE", str(cache_path))
    cache_path.write_text(
        json.dumps({"https://mine.example": {"token": "cached-token"}}),
        encoding="utf-8",
        newline="\n",
    )
    cache_path.chmod(0o644)

    assert token_cache_module._read_token_cache() == {
        "https://mine.example": {"token": "cached-token"}
    }
    assert token_cache_module._read_token_cache() == {
        "https://mine.example": {"token": "cached-token"}
    }

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("group/other-readable") == 1


def test_insecure_api_url_warning_goes_to_stderr_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        assert request.url.path == "/auth/login"
        login_count += 1
        return httpx.Response(200, json={"access_token": _jwt(exp=time.time() + 3600)})

    client = IssuekitClient(
        "http://mine.example",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.login(force=True)
    assert client.login(force=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("non-HTTPS transport") == 1
    assert "cleartext" in captured.err
    assert "ISSUEKIT_ALLOW_INSECURE=1" in captured.err
    assert login_count == 2


@pytest.mark.parametrize(
    "api_url",
    [
        "https://mine.example",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://127.12.34.56:8000",
        "http://[::1]:8000",
    ],
)
def test_insecure_api_url_warning_is_suppressed_for_https_and_loopback(
    api_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/login"
        return httpx.Response(200, json={"access_token": _jwt(exp=time.time() + 3600)})

    client = IssuekitClient(
        api_url,
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.login(force=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_insecure_api_url_warning_can_be_suppressed_by_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ISSUEKIT_ALLOW_INSECURE", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/login"
        return httpx.Response(200, json={"access_token": _jwt(exp=time.time() + 3600)})

    client = IssuekitClient(
        "http://mine.example",
        username="svc",
        password="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.login(force=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_bearer_request_warns_for_insecure_api_url_without_corrupting_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer static-token"
        return httpx.Response(200, json=[])

    client = IssuekitClient(
        "http://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_issues() == []

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "non-HTTPS transport" in captured.err


def test_windows_token_cache_acl_tightening_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_path = tmp_path / "token.json"
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object):
        calls.append(argv)
        if argv == ["whoami", "/user", "/fo", "csv"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout='"User Name","SID"\n"DOMAIN\\svc-user","S-1-5-21-123"\n',
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 5, stderr="access denied")

    monkeypatch.setenv("USERNAME", "svc-user")
    monkeypatch.setattr(token_cache_module, "_token_cache_path", lambda: cache_path)
    monkeypatch.setattr(token_cache_module.os, "name", "nt")
    monkeypatch.setattr(token_cache_module.subprocess, "run", fake_run)

    token_cache_module._write_token_cache({"https://mine.example": {"token": "cached-token"}})

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload == {"https://mine.example": {"token": "cached-token"}}
    assert calls == [
        ["whoami", "/user", "/fo", "csv"],
        [
            "icacls",
            str(cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-21-123:F",
        ],
        ["whoami", "/user", "/fo", "csv"],
        [
            "icacls",
            str(cache_path),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-21-123:F",
        ],
    ]
    assert "could not restrict API token cache permissions" in capsys.readouterr().err


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


def test_client_claim_sends_worker_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/issuekit/issues/7/claim"
        assert json.loads(request.content) == {
            "assignee": "codex",
            "worker": "machine/repo/checkout",
        }
        return httpx.Response(200, json={"id": 7, "stage": "implementing"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim(7, assignee="codex", worker="machine/repo/checkout") == {
        "id": 7,
        "stage": "implementing",
    }


def test_client_claim_omits_worker_when_not_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/issuekit/issues/7/claim"
        assert json.loads(request.content) == {"assignee": "codex"}
        return httpx.Response(200, json={"id": 7, "stage": "implementing"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim(7, assignee="codex") == {"id": 7, "stage": "implementing"}


def test_client_claim_sends_allow_self_implement_when_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/issuekit/issues/7/claim"
        assert json.loads(request.content) == {
            "assignee": "codex",
            "allow_self_implement": True,
        }
        return httpx.Response(200, json={"id": 7, "stage": "implementing"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim(7, assignee="codex", allow_self_implement=True) == {
        "id": 7,
        "stage": "implementing",
    }


def test_client_claim_omits_allow_self_implement_when_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert json.loads(request.content) == {"assignee": "codex"}
        return httpx.Response(200, json={"id": 7, "stage": "implementing"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim(7, assignee="codex", allow_self_implement=False) == {
        "id": 7,
        "stage": "implementing",
    }


def test_client_claim_next_sends_allow_self_implement_when_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/issuekit/issues/claim-next"
        assert json.loads(request.content) == {
            "assignee": "codex",
            "allow_self_implement": True,
        }
        return httpx.Response(200, json={"id": 7, "stage": "implementing"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim_next(assignee="codex", allow_self_implement=True) == {
        "id": 7,
        "stage": "implementing",
    }


def test_client_claim_next_sends_worker_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/issuekit/issues/claim-next"
        assert json.loads(request.content) == {
            "assignee": "codex",
            "priority": "high",
            "worker": "machine/repo/checkout",
        }
        return httpx.Response(200, json={"id": 7, "stage": "implementing"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.claim_next(
        assignee="codex",
        priority="high",
        worker="machine/repo/checkout",
    ) == {"id": 7, "stage": "implementing"}


def test_client_upsert_worker_posts_top_level_workers_endpoint() -> None:
    response = {
        "id": "machine/repo/checkout",
        "machine_id": "machine",
        "repo_id": "repo",
        "worker_id": "checkout",
        "path": "/repo",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/workers"
        assert json.loads(request.content) == {
            "machine_id": "machine",
            "repo_id": "repo",
            "worker_id": "checkout",
            "path": "/repo",
        }
        return httpx.Response(201, json=response)

    client = IssuekitClient(
        "https://mine.example",
        project="demo",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.upsert_worker(
        machine_id="machine",
        repo_id="repo",
        worker_id="checkout",
        path="/repo",
    ) == response


def test_client_upsert_worker_includes_role_and_description_when_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/workers"
        assert json.loads(request.content) == {
            "machine_id": "machine",
            "repo_id": "repo",
            "worker_id": "checkout",
            "path": "/repo",
            "role": "api-server",
            "description": "Hosts the API.",
        }
        return httpx.Response(201, json={"id": "machine/repo/checkout"})

    client = IssuekitClient(
        "https://mine.example",
        project="demo",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.upsert_worker(
        machine_id="machine",
        repo_id="repo",
        worker_id="checkout",
        path="/repo",
        role="api-server",
        description="Hosts the API.",
    )


def test_client_list_workers_parses_array_and_sends_filters() -> None:
    rows = [
        {
            "machine_id": "machine",
            "repo_id": "mine-py",
            "worker_id": "checkout",
            "path": "/repo",
            "role": "api-server",
            "description": "Hosts the API.",
            "last_seen": "2026-07-02T00:00:00Z",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/workers"
        assert dict(request.url.params) == {"repo_id": "mine-py", "project": "issuekit"}
        return httpx.Response(200, json=rows)

    client = IssuekitClient(
        "https://mine.example",
        project="demo",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_workers(repo_id="mine-py", project="issuekit") == rows


def test_client_list_workers_parses_paginated_items_envelope() -> None:
    rows = [{"machine_id": "m", "repo_id": "r", "worker_id": "w"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {}
        return httpx.Response(200, json={"items": rows, "total": 1})

    client = IssuekitClient(
        "https://mine.example",
        project="demo",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_workers() == rows


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


def test_client_create_proposal_accepts_dedup_200_response() -> None:
    response = {
        "id": 3,
        "target_project": "target",
        "origin": "source#0@abc123",
        "reply_to": None,
        "title": "Proposal",
        "body": "Body",
        "priority": None,
        "status": "pending",
        "created": "2026-06-30",
        "adopted_issue_number": None,
        "updated": "2026-06-30",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/target/proposals"
        assert json.loads(request.content) == {
            "origin": "source#0@abc123",
            "title": "Proposal",
            "body": "Body",
        }
        return httpx.Response(200, json=response)

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.create_proposal(origin="source#0@abc123", title="Proposal", body="Body") == response


def test_client_create_proposal_sends_thread_fields() -> None:
    response = {
        "id": 3,
        "thread_id": 9,
        "side": "frontend",
        "verdict": "propose",
        "contract": "GET /items",
        "origin": "source#0@abc123",
        "title": "Proposal",
        "body": "Body",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/target/proposals"
        assert json.loads(request.content) == {
            "origin": "source#0@abc123",
            "title": "Proposal",
            "body": "Body",
            "thread_id": 9,
            "side": "frontend",
            "verdict": "propose",
            "contract": "GET /items",
        }
        return httpx.Response(200, json=response)

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert (
        client.create_proposal(
            origin="source#0@abc123",
            title="Proposal",
            body="Body",
            thread_id=9,
            side="frontend",
            verdict="propose",
            contract="GET /items",
        )
        == response
    )


def test_client_create_proposal_sends_blocking_flag() -> None:
    response = {
        "id": 3,
        "origin": "source#0@abc123",
        "title": "Proposal",
        "body": "Body",
        "blocking": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/target/proposals"
        assert json.loads(request.content) == {
            "origin": "source#0@abc123",
            "title": "Proposal",
            "body": "Body",
            "blocking": True,
        }
        return httpx.Response(200, json=response)

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert (
        client.create_proposal(
            origin="source#0@abc123",
            title="Proposal",
            body="Body",
            blocking=True,
        )
        == response
    )


def test_client_create_proposal_sends_dependency_refs() -> None:
    response = {
        "id": 3,
        "origin": "source#0@abc123",
        "title": "Proposal",
        "body": "Body",
        "depends_on": ["mine-py#42"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/target/proposals"
        assert json.loads(request.content) == {
            "origin": "source#0@abc123",
            "title": "Proposal",
            "body": "Body",
            "depends_on": ["mine-py#42"],
        }
        return httpx.Response(200, json=response)

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert (
        client.create_proposal(
            origin="source#0@abc123",
            title="Proposal",
            body="Body",
            depends_on=["mine-py#42"],
        )
        == response
    )


def test_client_list_proposals_pages_wrapped_response() -> None:
    proposals = [{"id": proposal_id, "status": "pending"} for proposal_id in range(1, 6)]
    seen_pages: list[tuple[int, int, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params["limit"])
        offset = int(params["offset"])
        seen_pages.append((limit, offset, params["status"], params["thread_id"]))
        return httpx.Response(
            200,
            json={
                "items": proposals[offset : offset + limit],
                "total": len(proposals),
                "limit": limit,
                "offset": offset,
            },
        )

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_proposals(status="pending", thread_id=8, page_size=2) == proposals
    assert seen_pages == [(2, 0, "pending", "8"), (2, 2, "pending", "8"), (2, 4, "pending", "8")]


def test_client_proposal_thread_methods_use_expected_paths() -> None:
    seen: list[tuple[str, str, object]] = []
    threads = [{"id": thread_id, "status": "negotiating"} for thread_id in range(1, 5)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/4/reply"):
            return httpx.Response(200, json={"id": 5, "thread_id": 7, "side": "backend"})
        if request.url.path.endswith("/thread/7") and request.method == "GET":
            return httpx.Response(200, json={"id": 7, "status": "negotiating", "items": []})
        if request.url.path.endswith("/threads"):
            params = dict(request.url.params)
            limit = int(params["limit"])
            offset = int(params["offset"])
            return httpx.Response(
                200,
                json={
                    "items": threads[offset : offset + limit],
                    "total": len(threads),
                    "limit": limit,
                    "offset": offset,
                },
            )
        if request.url.path.endswith("/thread/7") and request.method == "PATCH":
            return httpx.Response(200, json={"id": 7, "status": "agreed", "agreed_contract": "GET /items"})
        raise AssertionError(request.url.path)

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.reply_proposal(
        4,
        origin="source#1",
        title="Reply",
        body="Body",
        side="backend",
        verdict="counter",
        contract="GET /items?page=1",
        priority="high",
    ) == {"id": 5, "thread_id": 7, "side": "backend"}
    assert client.get_thread(7) == {"id": 7, "status": "negotiating", "items": []}
    assert client.list_threads(status="negotiating", page_size=2) == threads
    assert client.patch_thread(7, status="agreed", agreed_contract="GET /items") == {
        "id": 7,
        "status": "agreed",
        "agreed_contract": "GET /items",
    }
    assert seen == [
        (
            "POST",
            "/api/issues/target/proposals/4/reply",
            {
                "origin": "source#1",
                "title": "Reply",
                "body": "Body",
                "side": "backend",
                "verdict": "counter",
                "contract": "GET /items?page=1",
                "priority": "high",
            },
        ),
        ("GET", "/api/issues/target/proposals/thread/7", None),
        ("GET", "/api/issues/target/proposals/threads", None),
        ("GET", "/api/issues/target/proposals/threads", None),
        ("PATCH", "/api/issues/target/proposals/thread/7", {"status": "agreed", "agreed_contract": "GET /items"}),
    ]


def test_client_proposal_get_adopt_and_discard_paths() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/adopt"):
            return httpx.Response(200, json={"id": 9, "title": "Adopted"})
        if request.url.path.endswith("/discard"):
            return httpx.Response(200, json={"id": 4, "status": "discarded"})
        return httpx.Response(200, json={"id": 4, "status": "pending"})

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.get_proposal(4) == {"id": 4, "status": "pending"}
    assert client.adopt_proposal(4, priority="high") == {"id": 9, "title": "Adopted"}
    assert client.discard_proposal(4) == {"id": 4, "status": "discarded"}
    assert seen == [
        ("GET", "/api/issues/target/proposals/4", None),
        ("POST", "/api/issues/target/proposals/4/adopt", {"priority": "high"}),
        ("POST", "/api/issues/target/proposals/4/discard", None),
    ]


def test_client_proposal_check_paths_and_payloads() -> None:
    seen: list[tuple[str, str, object, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, parse_qs(request.url.query.decode())))
        if request.url.path == "/api/issues/proposal-checks":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": 7,
                            "target_project": "target",
                            "proposal_id": 4,
                            "target_worker": "m/r/w",
                            "status": "pending",
                        }
                    ],
                    "total": 1,
                    "limit": 500,
                    "offset": 0,
                },
            )
        if request.url.path.endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "target_project": "target",
                    "proposal_id": 4,
                    "status": "answered",
                    "verdict": "approve",
                    "comment": "Looks good.",
                    "adopted_issue_ref": "target#9",
                },
            )
        return httpx.Response(
            201,
            json={
                "id": 7,
                "target_project": "target",
                "proposal_id": 4,
                "target_worker": "m/r/w",
                "status": "pending",
            },
        )

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.create_proposal_check(4, target_worker="m/r/w")["id"] == 7
    assert client.poll_proposal_checks(target_worker="m/r/w", status="pending")[0]["id"] == 7
    result = client.post_proposal_check_result(
        7,
        project="target",
        verdict="approve",
        comment="Looks good.",
        adopted_issue_ref="target#9",
    )

    assert result["status"] == "answered"
    assert seen == [
        (
            "POST",
            "/api/issues/target/proposals/4/checks",
            {"target_worker": "m/r/w"},
            {},
        ),
        (
            "GET",
            "/api/issues/proposal-checks",
            None,
            {"target_worker": ["m/r/w"], "status": ["pending"], "limit": ["50"], "offset": ["0"]},
        ),
        (
            "POST",
            "/api/issues/target/proposal-checks/7/result",
            {
                "verdict": "approve",
                "comment": "Looks good.",
                "adopted_issue_ref": "target#9",
            },
            {},
        ),
    ]


def test_client_import_proposals_posts_wrapped_body() -> None:
    items = [
        {
            "origin": "source#1@abc123",
            "reply_to": None,
            "created": None,
            "title": "Imported",
            "body": "Body",
            "status": "pending",
            "adopted_issue_number": None,
        }
    ]
    imported = [{"id": 8, **items[0]}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/issues/target/proposals/import"
        assert json.loads(request.content) == {"proposals": items}
        return httpx.Response(200, json=imported)

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.import_proposals(items) == imported


def test_client_proposal_errors_use_server_code_and_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "not_found", "message": "missing proposal"})

    client = IssuekitClient(
        "https://mine.example",
        project="target",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(WorkflowError) as excinfo:
        client.get_proposal(4)

    assert str(excinfo.value) == "missing proposal"
    assert excinfo.value.code == "not_found"


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


def test_client_reclaim_sends_expected_worker_guard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/7/reclaim"
        assert json.loads(request.content) == {
            "expected_worker": "machine/repo/dead",
        }
        return httpx.Response(200, json={"id": 7, "stage": "todo"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.reclaim(7, expected_worker="machine/repo/dead") == {
        "id": 7,
        "stage": "todo",
    }


def test_client_request_changes_sends_worker_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/7/request-changes"
        assert json.loads(request.content) == {
            "notes": "add tests",
            "reviewer": "codex",
            "assignee": "claude",
            "worker": "machine/repo/reviewer",
        }
        return httpx.Response(200, json={"id": 7, "stage": "changes_requested"})

    client = IssuekitClient(
        "https://mine.example",
        token="static-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.request_changes(
        7,
        notes="add tests",
        reviewer="codex",
        assignee="claude",
        worker="machine/repo/reviewer",
    ) == {"id": 7, "stage": "changes_requested"}


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


def test_client_approve_sends_worker_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/issues/issuekit/issues/7/approve"
        assert json.loads(request.content) == {
            "summary": "approved",
            "verification": "uv run python -m pytest",
            "reviewer": "codex",
            "worker": "machine/repo/reviewer",
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
        reviewer="codex",
        worker="machine/repo/reviewer",
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


def test_fake_issuekit_client_round_trips_proposal_lifecycle() -> None:
    client = FakeIssuekitClient()

    created = client.create_proposal(
        origin="source#0@abc123",
        title="Proposal",
        body="## Suggested Change\n\nDo this.",
    )
    duplicate = client.create_proposal(
        origin="source#0@abc123",
        title="Proposal",
        body="## Suggested Change\n\nDo this.",
    )
    listed = client.list_proposals(status="pending")
    adopted = client.adopt_proposal(created["id"], priority="low")
    discarded = client.create_proposal(origin="source#1@abc123", title="Discard", body="No.")
    discarded = client.discard_proposal(discarded["id"])

    assert duplicate["id"] == created["id"]
    assert listed == [created]
    assert adopted["title"] == "Proposal"
    assert adopted["priority"] == "low"
    assert client.get_proposal(created["id"])["status"] == "adopted"
    assert discarded["status"] == "discarded"
    assert client.list_proposals(status="pending") == []


def test_fake_issuekit_client_import_proposals_is_idempotent() -> None:
    client = FakeIssuekitClient()
    proposals = [
        {
            "origin": "source#1@abc123",
            "title": "Imported",
            "body": "Body",
            "status": "pending",
            "created": None,
            "reply_to": None,
            "adopted_issue_number": None,
        }
    ]

    first = client.import_proposals(proposals)
    second = client.import_proposals(proposals)

    assert first == second
    assert len(client.list_proposals(status="pending")) == 1


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
