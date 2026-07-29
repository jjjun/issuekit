"""Shared helpers for command implementations."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
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
