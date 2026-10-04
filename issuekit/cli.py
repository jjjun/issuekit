"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib import import_module

COMMAND_MODULES = {
    "info": "issuekit.commands.info",
    "show": "issuekit.commands.inspect",
    "next-review": "issuekit.commands.inspect",
    "add": "issuekit.commands.add",
    "login": "issuekit.commands.auth",
    "logout": "issuekit.commands.auth",
    "author-guard": "issuekit.commands.author_guard",
    "author": "issuekit.commands.author",
    "dispatch": "issuekit.commands.dispatch",
    "edit": "issuekit.commands.edit",
    "implement": "issuekit.commands.implement",
    "negotiate": "issuekit.commands.negotiate",
    "threads": "issuekit.commands.negotiate",
    "proposal-check-request": "issuekit.commands.proposal_check_request",
    "proposal-checks": "issuekit.commands.proposal_checks",
    "validate": "issuekit.commands.validate",
    "complete": "issuekit.commands.complete",
    "approve": "issuekit.commands.approve",
    "claim": "issuekit.commands.claim",
    "claims": "issuekit.commands.claims",
    "submit-review": "issuekit.commands.handoff",
    "request-changes": "issuekit.commands.handoff",
    "queue": "issuekit.commands.queue",
    "orphans": "issuekit.commands.orphans",
    "plan": "issuekit.commands.plan",
    "readdress": "issuekit.commands.readdress",
    "reclaim": "issuekit.commands.reclaim",
    "repos": "issuekit.commands.repos",
    "workers": "issuekit.commands.workers",
    "runs": "issuekit.commands.runs",
    "review": "issuekit.commands.review",
    "serve": "issuekit.commands.serve",
    "check-encoding": "issuekit.commands.check_encoding",
    "protocol": "issuekit.commands.protocol",
    "init": "issuekit.commands.init",
    "setup": "issuekit.commands.setup.command",
    "dev-tool": "issuekit.commands.dev_tool",
    "profile": "issuekit.commands.profile",
    "add-ref": "issuekit.commands.propose",
    "list-refs": "issuekit.commands.propose",
    "propose": "issuekit.commands.propose",
    "incoming": "issuekit.commands.propose",
    "outgoing": "issuekit.commands.propose",
    "adopt": "issuekit.commands.propose",
    "discard": "issuekit.commands.propose",
    "request": "issuekit.commands.request",
    "triage": "issuekit.commands.triage",
}


def _build_parser(module_paths: Sequence[str]) -> argparse.ArgumentParser:
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
    for module_path in module_paths:
        module = import_module(module_path)
        module.register(subparsers)
    return parser


def build_parser() -> argparse.ArgumentParser:
    return _build_parser(tuple(dict.fromkeys(COMMAND_MODULES.values())))


def main(argv: Sequence[str] | None = None) -> int:
    _configure_standard_streams()
    arguments = list(sys.argv[1:] if argv is None else argv)
    module_path = COMMAND_MODULES.get(arguments[0]) if arguments else None
    parser = (
        _build_parser((module_path,))
        if module_path is not None
        else build_parser()
    )
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        return int(exc.code)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        from issuekit.config.local import LocalConfigError
        from issuekit.errors import WorkflowError
        from issuekit.proposals.model import ProposalError

        if not isinstance(exc, (WorkflowError, ProposalError, LocalConfigError)):
            raise
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
