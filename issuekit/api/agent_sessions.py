"""Issue-owned agent runtime API resources."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from issuekit.core import drop_none
from issuekit.workflow import WorkflowError

from .base import JsonDict, ensure_dict


class AgentSessionResourceMixin:
    """Client methods for the issue agent-session v1 contract."""

    def create_agent_session(
        self, number: int, request: Mapping[str, Any]
    ) -> JsonDict:
        return self._agent_session_request(
            "POST", number, "", json=dict(request), label="Agent session response"
        )

    def list_agent_sessions(
        self,
        number: int,
        *,
        state: str | None = None,
        attempt: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> JsonDict:
        return self._agent_session_request(
            "GET",
            number,
            "",
            params=drop_none(
                {"state": state, "attempt": attempt, "limit": limit, "cursor": cursor}
            ),
            label="Agent session list response",
        )

    def get_agent_session(self, number: int, session_id: str) -> JsonDict:
        return self._agent_session_request(
            "GET", number, f"/{session_id}", label="Agent session response"
        )

    def acquire_agent_session_lease(
        self, number: int, session_id: str, request: Mapping[str, Any]
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/lease/acquire",
            json=dict(request),
            label="Agent session lease response",
        )

    def heartbeat_agent_session_lease(
        self,
        number: int,
        session_id: str,
        *,
        headers: Mapping[str, str],
        ttl_seconds: int | None = None,
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/lease/heartbeat",
            json=drop_none({"ttl_seconds": ttl_seconds}),
            headers=headers,
            label="Agent session lease response",
        )

    def release_agent_session_lease(
        self, number: int, session_id: str, *, headers: Mapping[str, str]
    ) -> None:
        self._authorized_request(
            "POST",
            self._agent_session_path(number, f"/{session_id}/lease/release"),
            json={},
            headers=headers,
        )

    def attach_native_agent_session(
        self,
        number: int,
        session_id: str,
        native_session_id: str,
        *,
        headers: Mapping[str, str],
        resume_from_session_id: str | None = None,
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/native-session",
            json=drop_none(
                {
                    "native_session_id": native_session_id,
                    "resume_from_session_id": resume_from_session_id,
                }
            ),
            headers=headers,
            label="Agent session response",
        )

    def create_agent_command(
        self,
        number: int,
        session_id: str,
        request: Mapping[str, Any],
    ) -> JsonDict:
        _validate_json_size(request, 64 * 1024, "Command payload")
        _validate_text_fields(request.get("payload"), 32 * 1024)
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/commands",
            json=dict(request),
            label="Agent command response",
        )

    def list_agent_commands(
        self,
        number: int,
        session_id: str,
        *,
        after: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> JsonDict:
        return self._agent_session_request(
            "GET",
            number,
            f"/{session_id}/commands",
            params=drop_none({"after": after, "state": state, "limit": limit}),
            label="Agent command list response",
        )

    def claim_agent_command(
        self, number: int, session_id: str, *, headers: Mapping[str, str]
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/commands/claim",
            json={"max_count": 1},
            headers=headers,
            label="Agent command claim response",
        )

    def acknowledge_agent_command(
        self,
        number: int,
        session_id: str,
        command_id: str,
        request: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/commands/{command_id}/ack",
            json=dict(request),
            headers=headers,
            label="Agent command response",
        )

    def append_agent_events(
        self,
        number: int,
        session_id: str,
        events: Sequence[Mapping[str, Any]],
        *,
        headers: Mapping[str, str],
    ) -> JsonDict:
        if not 1 <= len(events) <= 100:
            raise ValueError("Agent event batches require 1 to 100 events.")
        for event in events:
            _validate_json_size(event, 64 * 1024, "Agent event")
        _validate_json_size({"events": events}, 1024 * 1024, "Agent event batch")
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/events:batch",
            json={"events": [dict(event) for event in events]},
            headers=headers,
            label="Agent event batch response",
        )

    def list_agent_events(
        self,
        number: int,
        session_id: str,
        *,
        after: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> JsonDict:
        return self._agent_session_request(
            "GET",
            number,
            f"/{session_id}/events",
            params=drop_none(
                {"after": after, "event_type": event_type, "limit": limit}
            ),
            label="Agent event list response",
        )

    def seal_agent_session(
        self,
        number: int,
        session_id: str,
        reason: str,
        *,
        headers: Mapping[str, str],
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/seal",
            json={"reason": reason},
            headers=headers,
            label="Agent session response",
        )

    def close_agent_session(
        self,
        number: int,
        session_id: str,
        request: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
    ) -> JsonDict:
        return self._agent_session_request(
            "POST",
            number,
            f"/{session_id}/close",
            json=dict(request),
            headers=headers,
            label="Agent session response",
        )

    def _agent_session_request(
        self,
        method: str,
        number: int,
        suffix: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        label: str,
    ) -> JsonDict:
        payload = self._authorized_request(
            method,
            self._agent_session_path(number, suffix),
            json=json,
            params=params,
            headers=headers,
        )
        return ensure_dict(payload, label)

    def _agent_session_path(self, number: int, suffix: str) -> str:
        return (
            f"/api/issues/{self.project}/issues/{number}/agent-sessions{suffix}"
        )


def _validate_json_size(value: Any, maximum: int, label: str) -> None:
    size = len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if size > maximum:
        raise WorkflowError(
            f"{label} exceeds the {maximum}-byte limit.",
            code="payload_too_large",
        )


def _validate_text_fields(value: Any, maximum: int) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_text_fields(item, maximum)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_text_fields(item, maximum)
    elif isinstance(value, str) and len(value.encode("utf-8")) > maximum:
        raise WorkflowError(
            f"Command text exceeds the {maximum}-byte limit.",
            code="payload_too_large",
        )
