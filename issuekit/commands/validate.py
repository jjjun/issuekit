"""Implementation of the validate command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.store import ApiStore, get_store
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
        with get_store(config) as store:
            _validate_health(store)
            _, _, issues = store.read_all_issues()
    except (WorkflowError, ValueError) as exc:
        print(f"Error: API validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"API validation passed ({len(issues)} issues).")
    return 0


def _validate_health(store: ApiStore) -> None:
    payload = store.client.health()
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
