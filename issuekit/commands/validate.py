"""Implementation of the validate command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


def run(_args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        _, _, issues = get_store(config, issues_dir).read_all_issues()
    except (WorkflowError, ValueError) as exc:
        print(f"Error: API validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"API validation passed ({len(issues)} issues).")
    return 0
