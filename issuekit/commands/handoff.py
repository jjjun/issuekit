"""Implementation of review handoff commands."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.workflow import WorkflowError, request_changes, submit_for_review


def run_submit_review(args) -> int:
    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        issue = submit_for_review(
            issues_dir,
            issue_id,
            summary=args.summary,
            branch=args.branch,
            commit=args.commit,
            assignee=args.assignee,
            reviewer=args.reviewer,
            config=config,
        )
    except (TimeoutError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if config.use_filesystem_store:
        write_index_files(issues_dir, config.recent_count)
    print(
        f"id={issue.id} file={issue.relative_path} "
        f"assignee={issue.assignee} stage={issue.stage}"
    )
    return 0


def run_request_changes(args) -> int:
    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        issue = request_changes(
            issues_dir,
            issue_id,
            notes=args.notes,
            assignee=args.assignee,
            reviewer=args.reviewer,
            config=config,
        )
    except (TimeoutError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if config.use_filesystem_store:
        write_index_files(issues_dir, config.recent_count)
    print(
        f"id={issue.id} file={issue.relative_path} "
        f"assignee={issue.assignee} stage={issue.stage}"
    )
    return 0
