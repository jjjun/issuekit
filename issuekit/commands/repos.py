"""Implementation of repository registry maintenance commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.commands._common import print_json, run_command
from issuekit.config import load_config
from issuekit.errors import WorkflowError
from issuekit.workers.registry import (
    WorkerListingError,
    WorkerRemovalError,
    remove_api_repo,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    repos_parser = subparsers.add_parser(
        "repos",
        help="Maintain registered repository catalog entries.",
    )
    subcommands = repos_parser.add_subparsers(
        dest="repos_command",
        metavar="<subcommand>",
        required=True,
    )

    remove_parser = subcommands.add_parser(
        "remove",
        help="Remove a registered repo catalog entry.",
    )
    remove_parser.add_argument("repo", help="Repository key to remove.")
    remove_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    remove_parser.set_defaults(func=run_remove)


def run_remove(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        result = remove_api_repo(config, args.repo)
        if args.json:
            print_json(result.to_dict())
            return 0
        print(f"Removed repo {result.repo_key}.")
        return 0

    return run_command(
        action,
        errors=(WorkerListingError, WorkerRemovalError, WorkflowError, ValueError),
    )
