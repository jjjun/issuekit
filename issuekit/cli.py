"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from issuekit.commands import (
    check_encoding,
    claim,
    complete,
    generate_indexes,
    handoff,
    info,
    init,
    protocol,
    queue,
    validate,
)


COMMANDS = (
    "info",
    "validate",
    "generate-indexes",
    "complete",
    "claim",
    "submit-review",
    "request-changes",
    "queue",
    "check-encoding",
    "protocol",
    "init",
)


def _not_implemented(command_name: str):
    def handler(_args: argparse.Namespace) -> int:
        raise NotImplementedError(command_name)

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issuekit",
        description="Manage docs/issues local issue trackers.",
    )
    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        metavar="<command>",
        required=True,
    )

    info_parser = subparsers.add_parser("info", help="Show issue tracker status.")
    info_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    info_parser.set_defaults(func=info.run)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate issue files and generated indexes.",
    )
    validate_parser.set_defaults(func=validate.run)

    generate_indexes_parser = subparsers.add_parser(
        "generate-indexes",
        help="Generate docs/issues index files.",
    )
    generate_indexes_parser.set_defaults(func=generate_indexes.run)

    complete_parser = subparsers.add_parser(
        "complete",
        help="Complete an active issue.",
    )
    complete_parser.add_argument("id", help="Issue id to complete.")
    complete_parser.add_argument("--summary", help="Completion summary.")
    complete_parser.add_argument("--verification", help="Verification notes.")
    complete_parser.set_defaults(func=complete.run)

    claim_parser = subparsers.add_parser(
        "claim",
        help="Claim the next issue for an assignee.",
    )
    claim_parser.add_argument("--assignee", required=True, help="Assignee to claim for.")
    claim_parser.add_argument("--priority", choices=("high", "medium", "low"), help="Priority filter.")
    claim_parser.set_defaults(func=claim.run)

    submit_review_parser = subparsers.add_parser(
        "submit-review",
        help="Submit an issue for review.",
    )
    submit_review_parser.add_argument("id", help="Issue id to submit.")
    submit_review_parser.add_argument("--summary", required=True, help="ASCII handoff summary.")
    submit_review_parser.add_argument("--branch", help="Branch containing the implementation.")
    submit_review_parser.add_argument("--commit", help="Commit containing the implementation.")
    submit_review_parser.set_defaults(func=handoff.run_submit_review)

    request_changes_parser = subparsers.add_parser(
        "request-changes",
        help="Return an issue to codex with requested changes.",
    )
    request_changes_parser.add_argument("id", help="Issue id to return.")
    request_changes_parser.add_argument("--notes", required=True, help="ASCII review feedback.")
    request_changes_parser.set_defaults(func=handoff.run_request_changes)

    queue_parser = subparsers.add_parser(
        "queue",
        help="List active issues for an assignee.",
    )
    queue_parser.add_argument("--assignee", required=True, help="Assignee to list.")
    queue_parser.add_argument("--stage", help="Workflow stage filter.")
    queue_parser.set_defaults(func=queue.run)

    check_encoding_parser = subparsers.add_parser(
        "check-encoding",
        help="Check tracked files for encoding problems.",
    )
    check_encoding_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    check_encoding_parser.add_argument(
        "--no-mojibake",
        action="store_true",
        help="Disable likely mojibake text scanning.",
    )
    check_encoding_parser.add_argument(
        "--fix",
        action="store_true",
        help="Strip leading UTF-8 BOM bytes from tracked source files.",
    )
    check_encoding_parser.set_defaults(func=check_encoding.run)

    protocol_parser = subparsers.add_parser(
        "protocol",
        help="Print the current two-agent handoff protocol.",
    )
    protocol_parser.add_argument(
        "--agent",
        choices=("codex", "claude"),
        help="Print the protocol for one agent role.",
    )
    protocol_parser.set_defaults(func=protocol.run)

    init_parser = subparsers.add_parser(
        "init",
        help="Install docs/issues tracker templates in the current repository.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing templated files.",
    )
    init_parser.add_argument(
        "--with-mcp",
        action="store_true",
        help="Also scaffold MCP registration and thin agent protocol references.",
    )
    init_parser.set_defaults(func=init.run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    return args.func(args)
