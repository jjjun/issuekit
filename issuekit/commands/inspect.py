"""Read-only issue inspection commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.core import issue_dict, parse_issue_id_arg
from issuekit.store import get_store
from issuekit.workflow import WorkflowError, next_review


def register(subparsers: argparse._SubParsersAction) -> None:
    show_parser = subparsers.add_parser(
        "show",
        help="Read one active or completed issue without changing it.",
        description="Read one active or completed issue without changing it.",
    )
    show_parser.add_argument("id", help="Issue id to read.")
    show_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    show_parser.set_defaults(func=run_show)

    next_review_parser = subparsers.add_parser(
        "next-review",
        help="Read the next review-stage issue without changing issue state.",
        description="Read the next review-stage issue without changing issue state.",
    )
    next_review_parser.add_argument(
        "--reviewer",
        help="Reviewer assignee to inspect; defaults to the configured reviewer.",
    )
    next_review_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    next_review_parser.set_defaults(func=run_next_review)


def run_show(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        config = load_config(Path.cwd())
        with get_store(config) as store:
            issue = store.get_issue(issue_id)
        payload = (
            issue_dict(issue, include_body=True)
            if issue is not None
            else {"status": "none", "id": issue_id}
        )
        _print_issue(payload, as_json=args.json)
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))


def run_next_review(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        with get_store(config) as store:
            issue = next_review(args.reviewer, config=config, store=store)
        payload = (
            issue_dict(issue, include_body=True)
            if issue is not None
            else {
                "status": "none",
                "assignee": args.reviewer or config.default_reviewer,
                "stage": "review",
            }
        )
        _print_issue(payload, as_json=args.json)
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))


def _print_issue(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print_json(payload)
        return
    if payload["status"] == "none":
        if "id" in payload:
            print(f"Issue #{payload['id']} was not found.")
        else:
            print(
                "No review-stage issue found for "
                f"{payload.get('assignee') or 'the open review pool'}."
            )
        return

    print(f"Issue #{payload['id']}: {payload['title']}")
    for key, value in payload.items():
        if key in {"id", "title", "body"}:
            continue
        print(f"{key}={value}")
    print()
    print(payload["body"])
