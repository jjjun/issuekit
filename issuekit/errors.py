"""Shared error types used across issuekit layers."""

from collections.abc import Mapping
from typing import Any


class WorkflowError(RuntimeError):
    """Raised when a workflow transition cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def __str__(self) -> str:
        message = super().__str__()
        from issuekit.guards.separation import separation_guard_note

        note = separation_guard_note(message, code=self.code)
        if note is None or note in message:
            return message
        return f"{message}\n{note}"


AGENT_RUN_ERRORS = (FileNotFoundError, RuntimeError, TimeoutError, ValueError)
