"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from issuekit.commands import (
    add,
    approve,
    auth,
    author_guard,
    author,
    check_encoding,
    claim,
    complete,
    dev_tool,
    edit,
    handoff,
    implement,
    info,
    init,
    migrate_to_api,
    negotiate,
    orphans,
    proposal_checks,
    profile,
    propose,
    protocol,
    queue,
    reclaim,
    request,
    review,
    runs,
    serve,
    setup,
    triage,
    validate,
    workers,
)


COMMAND_MODULES = (
    info,
    add,
    auth,
    author_guard,
    author,
    edit,
    implement,
    negotiate,
    proposal_checks,
    validate,
    migrate_to_api,
    complete,
    approve,
    claim,
    handoff,
    queue,
    orphans,
    reclaim,
    workers,
    runs,
    review,
    serve,
    check_encoding,
    protocol,
    init,
    setup,
    dev_tool,
    profile,
    propose,
    request,
    triage,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issuekit",
        description="Manage API-backed issuekit trackers.",
    )
    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        metavar="<command>",
        required=True,
    )
    for module in COMMAND_MODULES:
        module.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    return args.func(args)
