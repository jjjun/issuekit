"""Shared helpers for command implementations."""

from __future__ import annotations

from collections.abc import Callable
import sys
from typing import TypeVar

from issuekit.core import has_non_ascii
from issuekit.workflow import WorkflowError


T = TypeVar("T")
CommandError = type[BaseException]
ErrorMessage = str | Callable[[BaseException], str]

STANDARD_COMMAND_ERRORS: tuple[CommandError, ...] = (
    ValueError,
    WorkflowError,
    UnicodeError,
)


def run_command(
    action: Callable[[], T],
    *,
    errors: tuple[CommandError, ...] = STANDARD_COMMAND_ERRORS,
    lookup_error: ErrorMessage | None = None,
) -> T | int:
    """Run a command action and map expected errors to CLI exit code 1."""

    try:
        return action()
    except errors as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except LookupError as exc:
        if lookup_error is None:
            raise
        print(_error_message(lookup_error, exc), file=sys.stderr)
        return 1


def active_issue_not_found(issue_id: int) -> str:
    return f"Active issue #{issue_id} was not found."


def require_ascii(*values: str, message: str) -> None:
    if any(has_non_ascii(value) for value in values):
        raise ValueError(message)


def _error_message(message: ErrorMessage, exc: BaseException) -> str:
    if callable(message):
        return message(exc)
    return message
