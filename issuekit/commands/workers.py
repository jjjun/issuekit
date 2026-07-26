"""Implementation of the workers listing command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.core import issue_dict, worker_display_from_row
from issuekit.workflow import WorkflowError
from issuekit.workers.registry import (
    WorkerListingError,
    WorkerPruneCandidate,
    WorkerPruneResult,
    WorkerRemovalError,
    WorkerRemovalResult,
    list_api_workers,
    prune_api_workers,
    remove_api_worker,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    workers_parser = subparsers.add_parser(
        "workers",
        help="List registered workers and their repo-level roles.",
    )
    workers_parser.add_argument("--repo-id", help="Filter workers by repo id.")
    workers_parser.add_argument("--project", help="Filter workers by project.")
    workers_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    workers_parser.set_defaults(func=run_list)
    subcommands = workers_parser.add_subparsers(
        dest="workers_command",
        metavar="<subcommand>",
    )

    remove_parser = subcommands.add_parser(
        "remove",
        help=(
            "Remove a registered worker by worker.repo or worker.repo@machine "
            "id."
        ),
    )
    remove_parser.add_argument("address", help="Worker address to remove.")
    remove_parser.add_argument(
        "--force",
        action="store_true",
        help="Remove even if the worker currently holds an implementing issue.",
    )
    remove_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    remove_parser.set_defaults(func=run_remove)

    prune_parser = subcommands.add_parser(
        "prune",
        help="Remove stale registered workers that hold no active or directed work.",
    )
    prune_parser.add_argument(
        "--stale-after-sec",
        type=float,
        default=300.0,
        help="Require last_seen to be older than this many seconds (default: 300).",
    )
    prune_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates without deleting them.",
    )
    prune_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    prune_parser.set_defaults(func=run_prune)


def run_list(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        workers = list_api_workers(
            config,
            repo_id=args.repo_id,
            project=args.project,
        )
        if args.json:
            print_json(workers)
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


def run_remove(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        result = remove_api_worker(config, args.address, force=args.force)
        if args.json:
            print_json(worker_removal_result_dict(result))
            return 0
        _print_removal_result(result)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkerRemovalError, WorkflowError, ValueError),
    )


def run_prune(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        preview = prune_api_workers(
            config,
            stale_after_sec=args.stale_after_sec,
            dry_run=True,
        )
        if args.dry_run:
            if args.json:
                print_json(worker_prune_result_dict(preview))
                return 0
            _print_prune_preview(preview)
            return 0
        _confirm_prune_count(len(preview.candidates))
        result = prune_api_workers(
            config,
            stale_after_sec=args.stale_after_sec,
            dry_run=False,
            expected_count=len(preview.candidates),
        )
        if args.json:
            print_json(worker_prune_result_dict(result))
            return 0
        _print_prune_result(result)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkerRemovalError, WorkflowError, ValueError),
    )


def worker_removal_result_dict(result: WorkerRemovalResult) -> dict[str, object]:
    return {
        "worker": result.worker,
        "display": worker_display_from_row(result.worker),
        "deleted": result.deleted,
        "implementing_issues": [
            issue_dict(issue) | {"worker": issue.worker}
            for issue in result.implementing_issues
        ],
    }


def worker_prune_result_dict(result: WorkerPruneResult) -> dict[str, object]:
    return {
        "dry_run": result.dry_run,
        "count": len(result.candidates),
        "candidates": [_candidate_dict(candidate) for candidate in result.candidates],
        "deleted": list(result.deleted),
    }


def _print_worker(worker: dict) -> None:
    key = worker_display_from_row(worker)
    role = worker.get("role") or "-"
    print(f"{key}  role={role}")
    details = []
    address = worker.get("address")
    if address:
        details.append(f"address={address}")
    machine_id = worker.get("machine_id")
    if machine_id:
        details.append(f"machine={machine_id}")
    path = worker.get("path")
    if path:
        details.append(f"path={path}")
    last_seen = worker.get("last_seen")
    if last_seen:
        details.append(f"last_seen={last_seen}")
    target_worker = worker.get("target_worker")
    if target_worker:
        details.append(f"target_worker={target_worker}")
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


def _print_removal_result(result: WorkerRemovalResult) -> None:
    worker = result.worker
    display = worker_display_from_row(worker)
    print(f"Removed worker {display}.")
    status = worker.get("status") or "-"
    last_seen = worker.get("last_seen") or "-"
    current = _current_issue_text(worker, result)
    print(f"  status={status}  last_seen={last_seen}  current_issue={current}")


def _print_prune_preview(result: WorkerPruneResult) -> None:
    if not result.candidates:
        print("No stale worker prune candidates.")
        return
    print(f"Stale worker prune candidates: {len(result.candidates)}")
    for candidate in result.candidates:
        _print_prune_candidate(candidate)


def _print_prune_result(result: WorkerPruneResult) -> None:
    if not result.candidates:
        print("No stale worker prune candidates.")
        return
    print(f"Removed stale workers: {len(result.deleted)}")
    for candidate in result.candidates:
        _print_prune_candidate(candidate)


def _print_prune_candidate(candidate: WorkerPruneCandidate) -> None:
    worker = candidate.worker
    print(
        f"- {worker_display_from_row(worker)} "
        f"(last_seen={worker.get('last_seen') or '-'}, "
        f"stale_seconds={int(candidate.stale_seconds)})"
    )


def _candidate_dict(candidate: WorkerPruneCandidate) -> dict[str, object]:
    return {
        "worker": candidate.worker,
        "display": worker_display_from_row(candidate.worker),
        "last_seen": candidate.worker.get("last_seen"),
        "stale_seconds": int(candidate.stale_seconds),
    }


def _current_issue_text(worker: dict, result: WorkerRemovalResult) -> str:
    if result.implementing_issues:
        return ", ".join(f"#{issue.id}" for issue in result.implementing_issues)
    current = worker.get("current_issue")
    return str(current) if current else "-"


def _confirm_prune_count(count: int) -> None:
    if count == 0:
        return
    response = input(f"Type {count} to delete {count} stale worker(s): ").strip()
    if response != str(count):
        raise WorkerRemovalError("Worker prune was not confirmed.")
