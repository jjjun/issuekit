"""Implementation of the edit command."""

from __future__ import annotations

import json
from pathlib import Path

from issuekit.commands._common import active_issue_not_found, require_ascii, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, VALID_ISSUE_PRIORITIES, issue_dict, parse_issue_id_arg
from issuekit.workflow import WorkflowError


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
            force=args.force,
            config=config,
        )
        if args.json:
            print(json.dumps(issue_dict(issue, include_body=True), indent=2))
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
    )
    config = config or IssuekitConfig()
    if store is None:
        from issuekit.store import get_store

        store = get_store(config)

    existing = store.get_issue(issue_id)
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
    return store.update_issue(
        issue_id,
        title=title.strip() if title is not None else None,
        body=update_body,
        priority=priority,
    )


def _validate_edit_input(
    *,
    title: str | None,
    body: str | None,
    body_file: str | None,
    append: str | None,
    append_file: str | None,
    priority: str | None,
) -> None:
    body_modes = [value is not None for value in (body, body_file, append, append_file)]
    if sum(body_modes) > 1:
        raise ValueError("Pass only one of --body, --body-file, --append, or --append-file.")
    if title is None and not any(body_modes) and priority is None:
        raise ValueError(
            "At least one of --title, --body, --body-file, --append, "
            "--append-file, or --priority is required."
        )
    if title is not None:
        if not title.strip():
            raise ValueError("--title is required.")
        require_ascii(title, message="--title must be ASCII-only.")
    if priority is not None and priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}")


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
