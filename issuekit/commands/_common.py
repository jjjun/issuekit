"""Shared helpers for command implementations."""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import FrameType
from typing import TypeVar

from issuekit.config import IssuekitConfig, has_local_project_context, load_config
from issuekit.core import is_valid_workflow_token
from issuekit.encoding import ASCII_ONLY_HINT, has_non_ascii
from issuekit.workflow import WorkflowError

T = TypeVar("T")
CommandError = type[BaseException]
ErrorMessage = str | Callable[[BaseException], str]

STANDARD_COMMAND_ERRORS: tuple[CommandError, ...] = (
    ValueError,
    WorkflowError,
    UnicodeError,
)


def print_json(payload: object) -> None:
    """Print a machine-readable command response to standard output."""

    print(json.dumps(payload, indent=2))


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
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        if lookup_error is None:
            raise
        print(f"error: {_error_message(lookup_error, exc)}", file=sys.stderr)
        return 1


def run_agent_command(
    action: Callable[[], T],
    *,
    errors: tuple[CommandError, ...] = STANDARD_COMMAND_ERRORS,
    lookup_error: ErrorMessage | None = None,
) -> T | int:
    """Run a one-shot agent command with SIGTERM translated into interruption."""

    with _interrupt_on_sigterm():
        return run_command(action, errors=errors, lookup_error=lookup_error)


@contextmanager
def _interrupt_on_sigterm():
    if os.name == "nt":
        yield
        return

    try:
        previous_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError, AttributeError):
        yield
        return

    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def active_issue_not_found(issue_id: int) -> str:
    return f"Active issue #{issue_id} was not found."


def read_text_file(path: str) -> str:
    """Read a prose file using the shared file-flag convention."""

    return Path(path).read_text(encoding="utf-8-sig").strip()


def require_ascii(*values: str, message: str) -> None:
    if any(has_non_ascii(value) for value in values):
        raise ValueError(f"{message} {ASCII_ONLY_HINT}")


def load_config_for_project_mutation(
    cwd: Path | str,
    *,
    command: str,
    project: str | None = None,
) -> IssuekitConfig:
    """Load config for project-scoped writes, failing closed outside a repo."""

    root = Path(cwd)
    config = load_config(root)
    if project is not None:
        project = project.strip()
        if not project or not is_valid_workflow_token(project):
            raise ValueError(f"Invalid --project token: {project}")
        return replace(config, project=project)
    if has_local_project_context(root):
        return config
    raise WorkflowError(
        f"`issuekit {command}` needs a local issuekit project context. Run it "
        "from a repo root with ISSUEKIT.md, issuekit.toml, or [tool.issuekit] "
        "in pyproject.toml, or pass --project <project> to target a project "
        "explicitly."
    )


def _error_message(message: ErrorMessage, exc: BaseException) -> str:
    if callable(message):
        return message(exc)
    return message
