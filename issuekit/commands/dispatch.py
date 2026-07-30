"""Implementation of the dispatch command."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, issue_dict
from issuekit.workers.addressing import target_worker_repo_id, validate_target_worker
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help="Direct an issue to a worker; use readdress to return it to the repo pool.",
    )
    dispatch_parser.add_argument("id", help="Issue id to dispatch.")
    dispatch_parser.add_argument(
        "--target-worker",
        required=True,
        help="Registered worker.repo or worker.repo@machine address.",
    )
    dispatch_parser.add_argument("--assignee", help="Optional implementer assignee.")
    dispatch_parser.add_argument(
        "--stage",
        choices=("todo", "planned"),
        help="Optional ready stage for the directed issue.",
    )
    dispatch_parser.add_argument(
        "--allow-unregistered-worker",
        action="store_true",
        help="Allow directing to a worker that has not registered yet.",
    )
    dispatch_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    dispatch_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        issue = dispatch_issue(
            int(args.id),
            target_worker=args.target_worker,
            assignee=args.assignee,
            stage=args.stage,
            allow_unregistered_worker=args.allow_unregistered_worker,
            config=config,
        )
        if args.json:
            output = issue_dict(issue)
            output["target_worker"] = issue.target_worker
            print_json(output)
            return 0
        print(f"Dispatched issue #{issue.id}: target_worker={issue.target_worker}")
        return 0

    return run_command(action, errors=(WorkflowError, ValueError))


def dispatch_issue(
    issue_id: int,
    *,
    target_worker: str,
    assignee: str | None = None,
    stage: str | None = None,
    allow_unregistered_worker: bool = False,
    config: IssuekitConfig | None = None,
    store=None,
) -> Issue:
    config = config or IssuekitConfig()
    if stage is not None and stage not in {"todo", "planned"}:
        raise WorkflowError(
            "Dispatch stage must be todo or planned.",
            code="invalid_stage",
        )
    from issuekit.store import get_store

    manager = get_store(config) if store is None else nullcontext(store)
    with manager as active_store:
        workers = active_store.list_workers(
            repo_id=target_worker_repo_id(target_worker),
            project=config.project,
        )
        validated_target = validate_target_worker(
            target_worker,
            config=config,
            workers=workers,
            allow_unregistered=allow_unregistered_worker,
        )
        return active_store.dispatch_issue(
            issue_id,
            target_worker=validated_target,
            assignee=assignee,
            stage=stage,
        )
