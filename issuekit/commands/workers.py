"""Implementation of the workers listing command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.worker_keys import worker_display_from_row
from issuekit.workflow import WorkflowError
from issuekit.worker_registry import WorkerListingError, list_api_workers


def register(subparsers: argparse._SubParsersAction) -> None:
    workers_parser = subparsers.add_parser(
        "workers",
        help="List registered workers and their repo-level roles.",
    )
    workers_parser.add_argument("--repo-id", help="Filter workers by repo id.")
    workers_parser.add_argument("--project", help="Filter workers by project.")
    workers_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    workers_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        workers = list_api_workers(
            config,
            repo_id=args.repo_id,
            project=args.project,
        )
        if args.json:
            print(json.dumps(workers, indent=2))
            return 0
        if not workers:
            print("No workers registered.")
            return 0
        for worker in workers:
            _print_worker(worker)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkflowError, ValueError),
    )


def _print_worker(worker: dict) -> None:
    key = worker_display_from_row(worker)
    role = worker.get("role") or "-"
    print(f"{key}  role={role}")
    details = []
    machine_id = worker.get("machine_id")
    if machine_id:
        details.append(f"machine={machine_id}")
    path = worker.get("path")
    if path:
        details.append(f"path={path}")
    last_seen = worker.get("last_seen")
    if last_seen:
        details.append(f"last_seen={last_seen}")
    if details:
        print(f"  {'  '.join(details)}")
    description = worker.get("description")
    if description:
        print(f"  {description}")
    repo_description = worker.get("repo_description")
    if repo_description:
        print(f"  repo: {repo_description}")
    for label, metadata in (
        ("repo_metadata", worker.get("repo_metadata")),
        ("worker_metadata", worker.get("worker_metadata")),
    ):
        if isinstance(metadata, dict) and metadata:
            values = "  ".join(f"{key}={metadata[key]}" for key in sorted(metadata))
            print(f"  {label}: {values}")
