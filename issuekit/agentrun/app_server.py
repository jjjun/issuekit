"""Codex App Server JSONL transport and normalized event handling."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from issuekit.file_permissions import chmod_600, open_owner_only

MAX_TEXT_CHARS = 32 * 1024
MAX_EVENT_BYTES = 64 * 1024
USAGE_TOKEN_FIELDS = (
    ("cachedInputTokens", "cached_input_tokens"),
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("reasoningOutputTokens", "reasoning_output_tokens"),
    ("totalTokens", "total_tokens"),
)
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "environment",
        "env",
        "lease_token",
        "token",
    }
)


class AppServerError(RuntimeError):
    """A local App Server process or protocol failure."""


class CommandJournal:
    """Append-only record written before a provider command has local effects."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            fd = open_owner_only(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(fd)
        chmod_600(path)

    def record(self, command: Mapping[str, Any]) -> None:
        entry = {
            "id": command.get("id"),
            "sequence": command.get("sequence"),
            "kind": command.get("kind"),
            "expected_turn_id": command.get("expected_turn_id"),
            "payload": redact_payload(command.get("payload")),
        }
        encoded = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def command_ids(self) -> set[str]:
        ids: set[str] = set()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ids
        for line in lines:
            try:
                command_id = json.loads(line).get("id")
            except (ValueError, AttributeError):
                continue
            if isinstance(command_id, str):
                ids.add(command_id)
        return ids


class AppServerTransport:
    """Synchronous JSON-RPC client over a local JSONL stdio process."""

    def __init__(
        self,
        binary: Path,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        stderr: TextIO,
        notification: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._notification = notification
        self._next_id = 1
        self._responses: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.process = subprocess.Popen(
            [str(binary), *argv],
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "issuekit",
                    "title": "issuekit",
                    "version": "0.1.0",
                }
            },
        )
        self.notify("initialized", {})

    def request(
        self, method: str, params: Mapping[str, Any] | None = None, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._responses[request_id] = response_queue
            self._write({"method": method, "id": request_id, "params": dict(params or {})})
        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise AppServerError(f"Codex App Server timed out handling {method}.") from exc
        finally:
            self._responses.pop(request_id, None)
        error = response.get("error")
        if isinstance(error, dict):
            message = error.get("message") or f"request {method} failed"
            raise AppServerError(f"Codex App Server: {message}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise AppServerError(f"Codex App Server returned an invalid {method} response.")
        return result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            self._write({"method": method, "params": dict(params or {})})

    def start_thread(
        self,
        *,
        cwd: Path,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "sandbox": "dangerFullAccess",
            "serviceName": "issuekit",
        }
        if model:
            params["model"] = model
        if reasoning_effort:
            params["config"] = {"model_reasoning_effort": reasoning_effort}
        return _thread_id(self.request("thread/start", params))

    def resume_thread(self, native_session_id: str, *, cwd: Path) -> str:
        return _thread_id(
            self.request(
                "thread/resume",
                {"threadId": native_session_id, "cwd": str(cwd)},
            )
        )

    def start_turn(self, thread_id: str, text: str) -> str:
        result = self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text[:MAX_TEXT_CHARS]}],
            },
        )
        turn = result.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise AppServerError("Codex App Server turn/start response omitted turn.id.")
        return turn["id"]

    def steer_turn(self, thread_id: str, turn_id: str, text: str) -> None:
        self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": text[:MAX_TEXT_CHARS]}],
            },
        )

    def interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )

    def close(self) -> int:
        if self._closed.is_set():
            return self.process.returncode or 0
        self._closed.set()
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            return self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                return self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                return self.process.wait()

    def _write(self, message: Mapping[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise AppServerError("Codex App Server is not running.")
        self.process.stdin.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self.process.stdin.flush()

    def _read_loop(self) -> None:
        if self.process.stdout is None:
            return
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            request_id = message.get("id")
            if isinstance(request_id, int) and (
                "result" in message or "error" in message
            ):
                response_queue = self._responses.get(request_id)
                if response_queue is not None:
                    response_queue.put(message)
                continue
            if isinstance(request_id, int) and isinstance(message.get("method"), str):
                self._deny_server_request(message)
                continue
            if self._notification is not None and isinstance(message.get("method"), str):
                self._notification(message)
        self._closed.set()

    def _deny_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message["id"]
        method = str(message.get("method"))
        if method.endswith("requestApproval"):
            result: dict[str, Any] = {"decision": "decline"}
        else:
            result = {"error": f"issuekit cannot handle server request {method}"}
        with self._lock:
            self._write({"id": request_id, "result": result})


def normalize_notification(
    message: Mapping[str, Any],
    *,
    event_key: str,
    command_id: str | None = None,
) -> dict[str, Any] | None:
    """Convert an App Server notification to a bounded provider event."""
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return None
    event_type = _event_type(method, params)
    if event_type is None:
        return None
    turn_id = _notification_turn_id(params)
    fields: dict[str, Any] = {
        "method": method,
        "item": _item_summary(params.get("item")),
        "status": _status(params),
        "message": _message_text(method, params),
    }
    usage = _token_usage(params)
    if usage is not None:
        fields["usage"] = usage
    payload = redact_payload(fields)
    event = {
        "event_key": event_key,
        "event_type": event_type,
        "turn_id": turn_id,
        "command_id": command_id,
        "payload": payload,
    }
    event = {key: value for key, value in event.items() if value is not None}
    encoded = json.dumps(event, ensure_ascii=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_EVENT_BYTES:
        event["payload"] = {"method": method, "truncated": True}
    return event


def redact_payload(value: Any) -> Any:
    """Remove secrets, raw file bodies, binaries, and unbounded values."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.lower().replace("-", "_")
            if normalized_key in SENSITIVE_KEYS or any(
                marker in normalized_key
                for marker in ("secret", "password", "credential", "lease_token")
            ):
                redacted[key] = "[redacted]"
            elif normalized_key in {"content", "file_content", "raw", "frame"}:
                redacted[key] = "[omitted]"
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_payload(item) for item in value[:100]]
    if isinstance(value, bytes):
        return "[binary omitted]"
    if isinstance(value, str):
        return value[:MAX_TEXT_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_TEXT_CHARS]


def _thread_id(result: Mapping[str, Any]) -> str:
    thread = result.get("thread")
    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
        raise AppServerError("Codex App Server thread response omitted thread.id.")
    return thread["id"]


def _event_type(method: str, params: Mapping[str, Any]) -> str | None:
    if method == "turn/started":
        return "turn_started"
    if method == "turn/completed":
        status = _status(params)
        if status == "interrupted":
            return "turn_interrupted"
        if status in {"failed", "error"}:
            return "turn_failed"
        return "turn_completed"
    if method == "item/started":
        return "tool_started"
    if method == "item/completed":
        item = params.get("item")
        if isinstance(item, dict) and item.get("type") == "agentMessage":
            return "assistant_message"
        return "tool_completed"
    if method == "item/agentMessage/delta":
        return "turn_progress"
    if method.endswith("/updated") or method.endswith("/delta"):
        return "turn_progress"
    if method in {"error", "thread/status/changed"}:
        return "diagnostic"
    return None


def _notification_turn_id(params: Mapping[str, Any]) -> str | None:
    turn = params.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("id"), str):
        return turn["id"]
    turn_id = params.get("turnId")
    return turn_id if isinstance(turn_id, str) else None


def _item_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: redact_payload(value.get(key))
        for key in ("id", "type", "status", "command", "name")
        if value.get(key) is not None
    }


def _status(params: Mapping[str, Any]) -> str | None:
    turn = params.get("turn")
    value = turn.get("status") if isinstance(turn, dict) else params.get("status")
    if isinstance(value, dict):
        value = value.get("type")
    return str(value) if value is not None else None


def _token_usage(params: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return normalized counts from a thread/tokenUsage/updated notification."""
    usage = params.get("tokenUsage")
    if not isinstance(usage, Mapping):
        return None
    normalized: dict[str, Any] = {}
    for scope in ("last", "total"):
        breakdown = _token_breakdown(usage.get(scope))
        if breakdown:
            normalized[scope] = breakdown
    window = _token_count(usage.get("modelContextWindow"))
    if window is not None:
        normalized["model_context_window"] = window
    return normalized or None


def _token_breakdown(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for source, name in USAGE_TOKEN_FIELDS:
        count = _token_count(value.get(source))
        if count is not None:
            counts[name] = count
    return counts


def _token_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _message_text(method: str, params: Mapping[str, Any]) -> str | None:
    if method != "item/agentMessage/delta":
        return None
    delta = params.get("delta")
    return delta[:MAX_TEXT_CHARS] if isinstance(delta, str) else None
