"""Implementation of the author command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.author_guard import create_author_guard, stop_message
from issuekit.commands._common import require_ascii, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    VALID_ISSUE_PRIORITIES,
    is_valid_workflow_token,
)
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    author_parser = subparsers.add_parser(
        "author",
        help="Create an active issue authored by an agent.",
    )
    author_parser.add_argument("--title", required=True, help="Issue title.")
    body_group = author_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Inline issue body.")
    body_group.add_argument("--body-file", help="File containing the issue body.")
    author_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Issue priority.",
    )
    author_parser.add_argument("--agent", required=True, help="Configured author agent.")
    author_parser.add_argument("--assign", help="Optional implementer assignee.")
    author_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        authored = author_issue(
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            priority=args.priority,
            agent=args.agent,
            assign=args.assign,
            config=config,
        )
        guard = create_author_guard(
            Path.cwd(),
            config=config,
            kind="issue",
            item_id=authored.id,
            ref=authored.ref,
            author_agent=args.agent,
        )

        print(f"Authored issue: {_authored_ref(authored)}")
        print(stop_message(guard))
        return 0

    return run_command(
        action,
        errors=(OSError, UnicodeError, ValueError, WorkflowError),
    )


def author_issue(
    *,
    title: str,
    body: str | None,
    body_file: str | None,
    priority: str,
    agent: str,
    assign: str | None = None,
    config: IssuekitConfig | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    _validate_author_input(
        title=title,
        priority=priority,
        agent=agent,
        assign=assign,
        config=config,
    )
    issue_body = _read_body(body=body, body_file=body_file)
    require_ascii(issue_body, message="--body and --body-file must be ASCII-only.")
    from issuekit.store import get_store

    store = get_store(config)
    return store.create_issue(  # type: ignore[attr-defined]
        title=title.strip(),
        body=issue_body.strip(),
        priority=priority,
        author=agent,
        assignee=assign,
    )


def _validate_author_input(
    *,
    title: str,
    priority: str,
    agent: str,
    assign: str | None,
    config: IssuekitConfig,
) -> None:
    if not title.strip():
        raise ValueError("--title is required.")
    require_ascii(title, message="--title must be ASCII-only.")
    if priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}")
    _validate_agent_token(agent, "--agent", config)
    if assign:
        _validate_agent_token(assign, "--assign", config)


def _validate_agent_token(value: str, label: str, config: IssuekitConfig) -> None:
    if not is_valid_workflow_token(value):
        raise WorkflowError(f"Invalid {label} token: {value}")
    if value not in config.assignees:
        raise WorkflowError(f"Unknown {label}: {value}")


def _read_body(*, body: str | None, body_file: str | None) -> str:
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    raise ValueError("--body or --body-file is required.")


def _authored_ref(authored: Issue) -> str:
    return authored.ref
