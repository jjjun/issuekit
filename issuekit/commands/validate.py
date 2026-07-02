"""Implementation of the validate command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate API connectivity and issue response shape.",
    )
    validate_parser.set_defaults(func=run)


def run(_args) -> int:
    config = load_config(Path.cwd())
    try:
        store = get_store(config)
        _validate_health(store)
        _, _, issues = store.read_all_issues()
    except (WorkflowError, ValueError) as exc:
        print(f"Error: API validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"API validation passed ({len(issues)} issues).")
    return 0


def _validate_health(store: object) -> None:
    client = getattr(store, "client", None)
    health = getattr(client, "health", None)
    if not callable(health):
        raise WorkflowError(
            "API client does not expose the health endpoint contract.",
            code="server_schema_drift",
        )
    payload = health()
    if not isinstance(payload, dict):
        raise WorkflowError(
            "Health response was not a JSON object.",
            code="invalid_response",
        )
    revision = payload.get("migration_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise WorkflowError(
            "Health response did not include migration_revision.",
            code="server_schema_drift",
        )
