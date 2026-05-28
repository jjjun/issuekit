"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from issuekit.commands import check_encoding, complete, generate_indexes, info, init, validate


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

    init_parser = subparsers.add_parser(
        "init",
        help="Install docs/issues tracker templates in the current repository.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing templated files.",
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
