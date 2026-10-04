"""Implementation of the workers listing command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.commands._common import add_json_flag, print_json, run_command
from issuekit.commands._heartbeat import warn_if_staleness_not_wider
from issuekit.config import load_config
from issuekit.core import worker_display_from_row
from issuekit.errors import WorkflowError
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
    add_json_flag(workers_parser)
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
    add_json_flag(remove_parser, suppress=True)
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
    prune_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion without an interactive prompt.",
    )
    add_json_flag(prune_parser, suppress=True)
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
    if args.repo_id is not None:
        print("error: --repo-id is not supported by workers remove", file=sys.stderr)
        return 2

    def action() -> int:
        config = load_config(Path.cwd())
        result = remove_api_worker(config, args.address, force=args.force)
        if args.json:
            print_json(result.to_dict())
            return 0
        _print_removal_result(result)
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkerRemovalError, WorkflowError, ValueError),
    )


def run_prune(args) -> int:
    if args.repo_id is not None:
        print("error: --repo-id is not supported by workers prune", file=sys.stderr)
        return 2

    def action() -> int:
        config = load_config(Path.cwd())
        warn_if_staleness_not_wider(
            args.stale_after_sec,
            config.worker_heartbeat_interval_sec,
        )
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
        _confirm_prune_count(len(preview.candidates), yes=args.yes)
        result = prune_api_workers(
            config,
            stale_after_sec=args.stale_after_sec,
            dry_run=False,
            expected_candidates=preview.candidates,
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


def worker_prune_result_dict(result: WorkerPruneResult) -> dict[str, object]:
    return {
        "dry_run": result.dry_run,
        "count": len(result.candidates),
        "candidates": [_candidate_dict(candidate) for candidate in result.candidates],
        "deleted": list(result.deleted),
        "skipped_projects": list(result.skipped_projects),
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
    _print_skipped_projects(result)
    if not result.candidates:
        print("No stale worker prune candidates.")
        return
    print(f"Stale worker prune candidates: {len(result.candidates)}")
    for candidate in result.candidates:
        _print_prune_candidate(candidate)


def _print_prune_result(result: WorkerPruneResult) -> None:
    _print_skipped_projects(result)
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


def _print_skipped_projects(result: WorkerPruneResult) -> None:
    for skipped in result.skipped_projects:
        print(
            f"Warning: skipped workers from project {skipped['project']} because "
            f"its issues could not be read: {skipped['error']}"
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


def _confirm_prune_count(count: int, *, yes: bool) -> None:
    if count == 0 or yes:
        return
    if not sys.stdin.isatty():
        raise WorkerRemovalError(
            "Worker prune requires --yes when stdin is non-interactive."
        )
    print(f"Type {count} to delete {count} stale worker(s): ", end="", file=sys.stderr)
    try:
        response = input().strip()
    except EOFError:
        response = ""
    if response != str(count):
        raise WorkerRemovalError("Worker prune was not confirmed.")
