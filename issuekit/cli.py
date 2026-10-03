"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from issuekit.commands import (
    add,
    approve,
    auth,
    author,
    author_guard,
    check_encoding,
    claim,
    claims,
    complete,
    dev_tool,
    dispatch,
    edit,
    handoff,
    implement,
    info,
    init,
    inspect,
    negotiate,
    orphans,
    plan,
    profile,
    proposal_check_request,
    proposal_checks,
    propose,
    protocol,
    queue,
    readdress,
    reclaim,
    repos,
    request,
    review,
    runs,
    serve,
    setup,
    triage,
    validate,
    workers,
)
from issuekit.config.local import LocalConfigError
from issuekit.proposals.model import ProposalError
from issuekit.workflow import WorkflowError

COMMAND_MODULES = (
    info,
    inspect,
    add,
    auth,
    author_guard,
    author,
    dispatch,
    edit,
    implement,
    negotiate,
    proposal_check_request,
    proposal_checks,
    validate,
    complete,
    approve,
    claim,
    claims,
    handoff,
    queue,
    orphans,
    plan,
    readdress,
    reclaim,
    repos,
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
    _configure_standard_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        return args.func(args)
    except (OSError, ValueError, WorkflowError, ProposalError, LocalConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, TypeError, ValueError):
            continue
