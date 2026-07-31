from __future__ import annotations

import json
import sys
from pathlib import Path

from issuekit.agentrun.app_server import (
    MAX_TEXT_CHARS,
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


def test_app_server_transport_initializes_starts_thread_and_turn(
    tmp_path: Path,
) -> None:
    server = tmp_path / "fake_app_server.py"
    server.write_text(
        (
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    message = json.loads(line)\n"
            "    if 'id' not in message:\n"
            "        continue\n"
            "    method = message['method']\n"
            "    if method == 'thread/start':\n"
            "        result = {'thread': {'id': 'thread-1'}}\n"
            "    elif method == 'turn/start':\n"
            "        result = {'turn': {'id': 'turn-1'}}\n"
            "    else:\n"
            "        result = {}\n"
            "    print(json.dumps({'id': message['id'], 'result': result}), flush=True)\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    notifications: list[dict[str, object]] = []

    with (tmp_path / "stderr.log").open("w", encoding="utf-8") as stderr:
        transport = AppServerTransport(
            Path(sys.executable),
            (str(server),),
            cwd=tmp_path,
            stderr=stderr,
            notification=notifications.append,
        )
        transport.initialize()
        thread_id = transport.start_thread(cwd=tmp_path, model="gpt-test")
        turn_id = transport.start_turn(thread_id, "Inspect the worktree.")
        assert transport.close() == 0

    assert thread_id == "thread-1"
    assert turn_id == "turn-1"
