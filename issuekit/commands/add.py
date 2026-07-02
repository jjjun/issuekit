"""Implementation of the worker registration command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.worker import WorkerRegistrationError, register_worker
from issuekit.worker_registry import try_post_worker_registration


def register(subparsers: argparse._SubParsersAction) -> None:
    add_parser = subparsers.add_parser(
        "add",
        aliases=("register",),
        help="Register this checkout as a local worker.",
    )
    add_parser.add_argument("--machine-id", help="Override the hostname-derived machine id.")
    add_parser.add_argument("--repo-id", help="Override the git-origin-derived repository id.")
    add_parser.add_argument("--worker-id", help="Override the checkout worker id.")
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
    print(f"machine_id = {identity.machine_id:<16} ({result.sources['machine_id']})")
    print(f"repo_id    = {identity.repo_id:<16} ({result.sources['repo_id']})")
    print(f"worker_id  = {identity.worker_id:<16} ({result.sources['worker_id']})")
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(f"Warning: worker registry update skipped: {exc}", file=sys.stderr)
        return 0

    try_post_worker_registration(
        config,
        cwd,
        on_error=lambda exc: print(
            f"Warning: worker registry update failed: {exc}",
            file=sys.stderr,
        ),
    )
    return 0
