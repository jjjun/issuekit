from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from textwrap import dedent

import pytest

import issuekit.agentrun.app_server as app_server
from issuekit.agentrun.app_server import (
    MAX_TEXT_CHARS,
    AppServerError,
    AppServerTransport,
    CommandJournal,
    normalize_notification,
    redact_payload,
)


def test_command_journal_is_durable_and_redacts_secrets(tmp_path: Path) -> None:
    path = tmp_path / "commands.jsonl"
    journal = CommandJournal(path)

    journal.record(
        {
            "id": "command-1",
            "sequence": 1,
            "kind": "turn_start",
            "expected_turn_id": None,
            "payload": {
                "text": "inspect the worktree",
                "lease_token": "do-not-store",
                "environment": {"API_KEY": "do-not-store"},
            },
        }
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["payload"]["lease_token"] == "[redacted]"
    assert row["payload"]["environment"] == "[redacted]"
    assert "do-not-store" not in path.read_text(encoding="utf-8")
    assert journal.command_ids() == {"command-1"}


def test_redact_payload_omits_raw_files_binary_and_bounds_text() -> None:
    redacted = redact_payload(
        {
            "authorization": "Bearer secret",
            "file_content": "private source",
            "binary": b"\x00\x01",
            "message": "x" * (MAX_TEXT_CHARS + 10),
        }
    )

    assert redacted["authorization"] == "[redacted]"
    assert redacted["file_content"] == "[omitted]"
    assert redacted["binary"] == "[binary omitted]"
    assert len(redacted["message"]) == MAX_TEXT_CHARS


def test_turn_text_truncation_logs_original_and_limited_lengths(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(app_server, "MAX_TEXT_CHARS", 5)
    transport = object.__new__(AppServerTransport)
    requests: list[tuple[str, dict[str, object]]] = []

    def request(method: str, params: dict[str, object]) -> dict[str, object]:
        requests.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    monkeypatch.setattr(transport, "request", request)
    with caplog.at_level(logging.WARNING, logger=app_server.__name__):
        assert transport.start_turn("thread-1", "abcdefgh") == "turn-1"
        transport.steer_turn("thread-1", "turn-1", "abcdefgh")

    assert [
        params["input"][0]["text"] for _, params in requests
    ] == ["abcde", "abcde"]
    assert caplog.messages == [
        "Truncating Codex App Server turn/start input from 8 to 5 characters.",
        "Truncating Codex App Server turn/steer input from 8 to 5 characters.",
    ]


def test_resume_thread_keeps_issuekit_permission_settings(
    tmp_path: Path, monkeypatch
) -> None:
    transport = object.__new__(AppServerTransport)
    requests: list[tuple[str, dict[str, object]]] = []

    def request(method: str, params: dict[str, object]) -> dict[str, object]:
        requests.append((method, params))
        return {"thread": {"id": "thread-1"}}

    monkeypatch.setattr(transport, "request", request)

    assert transport.resume_thread("native-session-1", cwd=tmp_path) == "thread-1"
    assert requests == [
        (
            "thread/resume",
            {
                "threadId": "native-session-1",
                "cwd": str(tmp_path),
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            },
        )
    ]


def test_app_server_transport_answers_server_requests_with_valid_responses(
    tmp_path: Path,
) -> None:
    server = tmp_path / "fake_server_requests.py"
    captured = tmp_path / "server-responses.json"
    server.write_text(
        dedent(
            """\
            import json, sys
            requests = [
                {'id': 100, 'method': 'item/tool/requestUserInput'},
                {'id': 101, 'method': 'mcpServer/elicitation/request'},
                {'id': 102, 'method': 'item/permissions/requestApproval'},
                {'id': 103, 'method': 'item/commandExecution/requestApproval'},
            ]
            responses = []
            for line in sys.stdin:
                message = json.loads(line)
                if 'id' not in message:
                    continue
                if message.get('method') == 'initialize':
                    print(json.dumps({'id': message['id'], 'result': {}}), flush=True)
                    for request in requests:
                        print(json.dumps(request), flush=True)
                elif message.get('id') in {100, 101, 102, 103}:
                    responses.append(message)
                    if len(responses) == len(requests):
                        with open(sys.argv[1], 'w', encoding='utf-8') as stream:
                            json.dump(responses, stream)
            """
        ),
        encoding="utf-8",
        newline="\n",
    )

    with (tmp_path / "stderr.log").open("w", encoding="utf-8") as stderr:
        transport = AppServerTransport(
            Path(sys.executable),
            (str(server), str(captured)),
            cwd=tmp_path,
            stderr=stderr,
        )
        transport.initialize()
        deadline = time.monotonic() + 2
        while not captured.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert transport.close() == 0

    responses = {
        message["id"]: message for message in json.loads(captured.read_text("utf-8"))
    }
    assert responses[100]["error"]["code"] == -32601
    assert responses[101]["error"]["code"] == -32601
    assert responses[102]["result"] == {"permissions": {}}
    assert responses[103]["result"] == {"decision": "decline"}


def test_normalize_notification_maps_turn_and_agent_message_events() -> None:
    started = normalize_notification(
        {
            "method": "turn/started",
            "params": {"turn": {"id": "turn-1", "status": "inProgress"}},
        },
        event_key="session:1",
        command_id="command-1",
    )
    message = normalize_notification(
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "id": "item-1",
                    "type": "agentMessage",
                    "content": "raw message must not be uploaded",
                },
            },
        },
        event_key="session:2",
        command_id="command-1",
    )

    assert started == {
        "event_key": "session:1",
        "event_type": "turn_started",
        "turn_id": "turn-1",
        "command_id": "command-1",
        "payload": {"method": "turn/started", "item": None, "status": "inProgress", "message": None},
    }
    assert message is not None
    assert message["event_type"] == "assistant_message"
    assert message["payload"]["item"] == {
        "id": "item-1",
        "type": "agentMessage",
    }
    assert "raw message" not in json.dumps(message)


def test_normalize_notification_records_token_usage() -> None:
    event = normalize_notification(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {
                    "last": {
                        "cachedInputTokens": 8,
                        "inputTokens": 10,
                        "outputTokens": 4,
                        "reasoningOutputTokens": 2,
                        "totalTokens": 14,
                    },
                    "total": {
                        "cachedInputTokens": 16,
                        "inputTokens": 30,
                        "outputTokens": 12,
                        "reasoningOutputTokens": 5,
                        "totalTokens": 42,
                    },
                    "modelContextWindow": 272000,
                },
            },
        },
        event_key="session:3",
    )

    assert event is not None
    assert event["event_type"] == "turn_progress"
    assert event["turn_id"] == "turn-1"
    assert event["payload"]["usage"] == {
        "last": {
            "cached_input_tokens": 8,
            "input_tokens": 10,
            "output_tokens": 4,
            "reasoning_output_tokens": 2,
            "total_tokens": 14,
        },
        "total": {
            "cached_input_tokens": 16,
            "input_tokens": 30,
            "output_tokens": 12,
            "reasoning_output_tokens": 5,
            "total_tokens": 42,
        },
        "model_context_window": 272000,
    }


def test_normalize_notification_omits_usage_without_token_counts() -> None:
    event = normalize_notification(
        {"method": "turn/started", "params": {"turn": {"id": "turn-1"}}},
        event_key="session:4",
    )

    assert event is not None
    assert "usage" not in event["payload"]


@pytest.mark.parametrize(
    ("message", "event_type", "failure_reason"),
    [
        (
            {
                "method": "turn/completed",
                "params": {
                    "turn": {
                        "id": "turn-1",
                        "status": "failed",
                        "error": {"message": "turn failed"},
                    }
                },
            },
            "turn_failed",
            "turn failed",
        ),
        (
            {
                "method": "error",
                "params": {"willRetry": False, "error": {"message": "fatal error"}},
            },
            "turn_failed",
            "fatal error",
        ),
    ],
)
def test_normalize_notification_preserves_non_retryable_failure_details(
    message: dict[str, object], event_type: str, failure_reason: str
) -> None:
    event = normalize_notification(message, event_key="session:failure")

    assert event is not None
    assert event["event_type"] == event_type
    assert event["payload"]["message"] == failure_reason


def test_app_server_transport_initializes_starts_thread_and_turn(
    tmp_path: Path, monkeypatch
) -> None:
    server = tmp_path / "fake_app_server.py"
    captured_params = tmp_path / "thread-start.json"
    server.write_text(
        (
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    message = json.loads(line)\n"
            "    if 'id' not in message:\n"
            "        continue\n"
            "    method = message['method']\n"
            "    if method == 'thread/start':\n"
            "        params = message.get('params', {})\n"
            "        if params.get('sandbox') not in {'read-only', 'workspace-write', 'danger-full-access'}:\n"
            "            response = {'id': message['id'], 'error': {'code': -32600, 'message': \"Invalid request: unknown variant\"}}\n"
            "        else:\n"
            "            with open(sys.argv[1], 'w', encoding='utf-8') as stream:\n"
            "                json.dump(params, stream)\n"
            "            response = {'id': message['id'], 'result': {'thread': {'id': 'thread-1'}}}\n"
            "    elif method == 'turn/start':\n"
            "        response = {'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}}\n"
            "    else:\n"
            "        response = {'id': message['id'], 'result': {}}\n"
            "    print(json.dumps(response), flush=True)\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    notifications: list[dict[str, object]] = []

    with (tmp_path / "stderr.log").open("w", encoding="utf-8") as stderr:
        transport = AppServerTransport(
            Path(sys.executable),
            (str(server), str(captured_params)),
            cwd=tmp_path,
            stderr=stderr,
            notification=notifications.append,
        )
        transport.initialize()
        thread_id = transport.start_thread(cwd=tmp_path, model="gpt-test")
        monkeypatch.setattr(
            "issuekit.agentrun.app_server.DANGER_FULL_ACCESS_SANDBOX",
            "dangerFullAccess",
        )
        with pytest.raises(AppServerError, match="unknown variant"):
            transport.start_thread(cwd=tmp_path, model="gpt-test")
        turn_id = transport.start_turn(thread_id, "Inspect the worktree.")
        assert transport.close() == 0

    assert thread_id == "thread-1"
    assert turn_id == "turn-1"
    assert json.loads(captured_params.read_text(encoding="utf-8")) == {
        "cwd": str(tmp_path),
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
        "serviceName": "issuekit",
        "model": "gpt-test",
    }
