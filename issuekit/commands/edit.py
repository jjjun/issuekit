"""Implementation of the edit command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from issuekit.commands._common import (
    active_issue_not_found,
    print_json,
    require_ascii,
    run_command,
)
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, VALID_ISSUE_PRIORITIES, issue_dict, parse_issue_id_arg
from issuekit.issues.dependencies import dependency_refs
from issuekit.store import managed_issue_store
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    edit_parser = subparsers.add_parser(
        "edit",
        help="Edit an API-backed issue title, body, or priority.",
    )
    edit_parser.add_argument("id", help="Issue id to edit.")
    edit_parser.add_argument("--title", help="Replacement issue title.")
    edit_body_group = edit_parser.add_mutually_exclusive_group()
    edit_body_group.add_argument("--body", help="Replacement inline issue body.")
    edit_body_group.add_argument("--body-file", help="File containing replacement issue body.")
    edit_body_group.add_argument("--append", help="Inline text to append to the issue body.")
    edit_body_group.add_argument("--append-file", help="File containing text to append to the issue body.")
    edit_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        help="Replacement issue priority.",
    )
    edit_parser.add_argument(
        "--depends-on",
        action="append",
        dest="depends_on",
        help="Replace upstream dependency refs with one or more project#proposal:123 values.",
    )
    edit_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow editing an issue that is already in flight.",
    )
    edit_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    edit_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        issue = edit_issue(
            parse_issue_id_arg(args.id),
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            append=args.append,
            append_file=args.append_file,
            priority=args.priority,
            depends_on=args.depends_on,
            force=args.force,
            config=config,
        )
        if args.json:
            print_json(issue_dict(issue, include_body=True))
        else:
            print(f"Updated issue: {issue.ref}")
        return 0

    return run_command(
        action,
        errors=(OSError, UnicodeError, ValueError, WorkflowError),
    )


def edit_issue(
    issue_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    body_file: str | None = None,
    append: str | None = None,
    append_file: str | None = None,
    priority: str | None = None,
    depends_on: str | Sequence[str] | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
    store=None,
) -> Issue:
    _validate_edit_input(
        title=title,
        body=body,
        body_file=body_file,
        append=append,
        append_file=append_file,
        priority=priority,
        depends_on=depends_on,
    )
    config = config or IssuekitConfig()

    with managed_issue_store(config, store) as active_store:
        existing = active_store.get_issue(issue_id)
        if existing is None:
            raise ValueError(active_issue_not_found(issue_id))
        if existing.issue_status == "completed":
            raise WorkflowError(f"Issue #{issue_id} is completed and cannot be edited.")
        stage = existing.stage or "todo"
        if stage != "todo" and not force:
            raise WorkflowError(
                f"Issue #{issue_id} is at stage {stage}; "
                "pass --force to edit an issue that is already in flight."
            )

        update_body = _body_update(
            existing=existing,
            body=body,
            body_file=body_file,
            append=append,
            append_file=append_file,
        )
        return active_store.update_issue(
            issue_id,
            title=title.strip() if title is not None else None,
            body=update_body,
            priority=priority,
            depends_on=_depends_on(depends_on) if depends_on is not None else None,
        )


def _validate_edit_input(
    *,
    title: str | None,
    body: str | None,
    body_file: str | None,
    append: str | None,
    append_file: str | None,
    priority: str | None,
    depends_on: str | Sequence[str] | None,
) -> None:
    body_modes = [value is not None for value in (body, body_file, append, append_file)]
    if sum(body_modes) > 1:
        raise ValueError("Pass only one of --body, --body-file, --append, or --append-file.")
    if title is None and not any(body_modes) and priority is None and depends_on is None:
        raise ValueError(
            "At least one of --title, --body, --body-file, --append, "
            "--append-file, --priority, or --depends-on is required."
        )
    if title is not None:
        if not title.strip():
            raise ValueError("--title is required.")
        require_ascii(title, message="--title must be ASCII-only.")
    if priority is not None and priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}")
    if depends_on is not None:
        _depends_on(depends_on)


def _body_update(
    *,
    existing: Issue,
    body: str | None,
    body_file: str | None,
    append: str | None,
    append_file: str | None,
) -> str | None:
    if body is not None:
        update_body = body.strip()
        require_ascii(update_body, message="--body and --body-file must be ASCII-only.")
        return update_body
    if body_file is not None:
        update_body = Path(body_file).read_text(encoding="utf-8-sig").strip()
        require_ascii(update_body, message="--body and --body-file must be ASCII-only.")
        return update_body
    if append is not None:
        append_body = append.strip()
        require_ascii(append_body, message="--append and --append-file must be ASCII-only.")
        return f"{existing.body}\n\n{append_body}"
    if append_file is not None:
        append_body = Path(append_file).read_text(encoding="utf-8-sig").strip()
        require_ascii(append_body, message="--append and --append-file must be ASCII-only.")
        return f"{existing.body}\n\n{append_body}"
    return None


def _depends_on(value: str | Sequence[str]) -> tuple[str, ...]:
    try:
        return dependency_refs(value)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
