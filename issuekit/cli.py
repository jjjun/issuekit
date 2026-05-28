"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from issuekit.commands import complete, generate_indexes, info, validate


COMMANDS = (
    "info",
    "validate",
    "generate-indexes",
    "complete",
    "check-encoding",
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

    check_encoding_parser = subparsers.add_parser(
        "check-encoding",
        help="Check tracked files for encoding problems.",
    )
    check_encoding_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    check_encoding_parser.set_defaults(func=_not_implemented("check-encoding"))

    init_parser = subparsers.add_parser(
        "init",
        help="Install docs/issues tracker templates in the current repository.",
    )
    init_parser.set_defaults(func=_not_implemented("init"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    return args.func(args)
