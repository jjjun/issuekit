"""Implementation of the worker registration command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.worker import WorkerRegistrationError, register_worker


def run(args) -> int:
    try:
        result = register_worker(
            Path.cwd(),
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
    return 0
