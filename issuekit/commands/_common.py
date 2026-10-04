"""Shared helpers for command implementations."""

from __future__ import annotations

import argparse
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
from issuekit.errors import WorkflowError
from issuekit.signals import installed_signal_handlers

T = TypeVar("T")
CommandError = type[BaseException]
ErrorMessage = str | Callable[[BaseException], str]

STANDARD_COMMAND_ERRORS: tuple[CommandError, ...] = (
    ValueError,
    WorkflowError,
    UnicodeError,
)


def add_json_flag(
    parser: argparse.ArgumentParser,
    *,
    suppress: bool = False,
    help: str = "Print JSON output.",
) -> None:
    """Add the standard JSON output flag, optionally preserving a parent value."""

    if suppress:
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help=help,
        )
    else:
        parser.add_argument("--json", action="store_true", help=help)


def add_agent_option(
    parser: argparse.ArgumentParser,
    *,
    help: str,
    required: bool = False,
) -> None:
    """Add the configured agent selection option."""

    parser.add_argument("--agent", required=required, help=help)


def add_model_options(
    parser: argparse.ArgumentParser,
    *,
    model_help: str,
    effort_help: str,
) -> None:
    """Add model and reasoning-effort overrides."""

    parser.add_argument("--model", help=model_help)
    parser.add_argument("--reasoning-effort", help=effort_help)


def add_timeout_option(
    parser: argparse.ArgumentParser,
    *,
    default: float,
    help: str,
) -> None:
    """Add the agent run timeout option."""

    parser.add_argument("--timeout-sec", type=float, default=default, help=help)


def add_agent_run_options(
    parser: argparse.ArgumentParser,
    *,
    agent_help: str,
    agent_required: bool = False,
    model_help: str,
    effort_help: str,
    timeout_default: float,
    timeout_help: str,
) -> None:
    """Add the standard agent, model, reasoning-effort, and timeout options."""

    add_agent_option(parser, help=agent_help, required=agent_required)
    add_model_options(parser, model_help=model_help, effort_help=effort_help)
    add_timeout_option(parser, default=timeout_default, help=timeout_help)


def add_guard_override_flags(
    parser: argparse.ArgumentParser,
    *,
    author_session: bool = True,
    any_branch: bool = True,
    sync: bool = True,
) -> None:
    """Add the selected local guard recovery flags."""

    if author_session:
        parser.add_argument(
            "--allow-author-session",
            action="store_true",
            help="Override a local author-session STOP guard for human recovery.",
        )
    if any_branch:
        parser.add_argument(
            "--allow-any-branch",
            action="store_true",
            help="Override the configured work_branch guard for human recovery.",
        )
    if sync:
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Skip the claim-time clean checkout and fast-forward sync guard.",
        )


def add_follow_flag(parser: argparse.ArgumentParser, *, help: str) -> None:
    """Add the live agent-run heartbeat flag."""

    parser.add_argument("--follow", action="store_true", help=help)


def add_text_file_pair(
    target: argparse._ActionsContainer,
    name: str,
    *,
    help: str,
    file_help: str,
) -> None:
    """Add inline and file options for one text input."""

    target.add_argument(f"--{name}", help=help)
    target.add_argument(f"--{name}-file", help=file_help)


def add_exclusive_text_file_pair(
    parser: argparse.ArgumentParser,
    name: str,
    *,
    help: str,
    file_help: str,
    required: bool = False,
) -> None:
    """Add mutually exclusive inline and file options for one text input."""

    group = parser.add_mutually_exclusive_group(required=required)
    add_text_file_pair(group, name, help=help, file_help=file_help)


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

    with installed_signal_handlers({signal.SIGTERM: _handle_sigterm}):
        yield


def _handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


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
