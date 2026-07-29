"""Implementation of the worker registration command."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from issuekit.config import load_config
from issuekit.core import is_valid_workflow_token
from issuekit.workers.identity import WorkerRegistrationError, register_worker
from issuekit.workers.registry import try_post_worker_registration


def register(subparsers: argparse._SubParsersAction) -> None:
    add_parser = subparsers.add_parser(
        "add",
        aliases=("register",),
        help="Register this checkout as a local worker.",
    )
    add_parser.add_argument("--machine-id", help="Override the hostname-derived machine id.")
    add_parser.add_argument("--repo-id", help="Override the git-origin-derived repository id.")
    add_parser.add_argument("--worker-id", help="Override the checkout worker id.")
    add_parser.add_argument("--repo-description", help="Repo description to publish.")
    add_parser.add_argument(
        "--repo-metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Repo metadata entry to publish; repeat for multiple entries.",
    )
    add_parser.add_argument(
        "--worker-metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Worker metadata entry to publish; repeat for multiple entries.",
    )
    add_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing pinned worker id or local collision.",
    )
    add_parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    try:
        result = register_worker(
            cwd,
            machine_id=args.machine_id,
            repo_id=args.repo_id,
            worker_id=args.worker_id,
            force=args.force,
        )
    except WorkerRegistrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    identity = result.identity
    worker_display = f"{identity.worker_name}.{identity.repo_id}"
    print(f"worker      = {worker_display:<16} (worker.repo)")
    print(f"repo_id     = {identity.repo_id:<16} ({result.sources['repo_id']})")
    print(f"worker_name = {identity.worker_name:<16} ({result.sources['worker_id']})")
    print(f"machine_id  = {identity.machine_id:<16} ({result.sources['machine_id']})")
    if result.canonical_url:
        print(f"canonical_url = {result.canonical_url}")
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(f"Warning: worker registry update skipped: {exc}", file=sys.stderr)
        return 0
    try:
        config = _apply_metadata_flags(config, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try_post_worker_registration(
        config,
        cwd,
        canonical_url=result.canonical_url,
        on_error=lambda exc: print(
            f"Warning: worker registry update failed: {exc}",
            file=sys.stderr,
        ),
    )
    return 0


def _apply_metadata_flags(config, args):
    repo_metadata = dict(config.repo_metadata)
    worker_metadata = dict(config.worker_metadata)
    repo_metadata.update(_metadata_flags(args.repo_metadata, "--repo-metadata"))
    worker_metadata.update(_metadata_flags(args.worker_metadata, "--worker-metadata"))
    return replace(
        config,
        repo_description=args.repo_description or config.repo_description,
        repo_metadata=repo_metadata,
        worker_metadata=worker_metadata,
    )


def _metadata_flags(entries: list[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        key = key.strip()
        if not separator or not key or not is_valid_workflow_token(key):
            raise ValueError(f"{label} entries must use KEY=VALUE with token keys.")
        parsed[key] = value.strip()
    return parsed
