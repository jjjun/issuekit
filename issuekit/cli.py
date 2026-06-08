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
    implement,
    info,
    init,
    propose,
    protocol,
    queue,
    setup,
    validate,
)


COMMANDS = (
    "info",
    "implement",
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
    "setup",
    "add-ref",
    "list-refs",
    "propose",
    "incoming",
    "adopt",
    "discard",
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

    implement_parser = subparsers.add_parser(
        "implement",
        help="Drive an agent to implement an active issue.",
    )
    implement_parser.add_argument("id", help="Issue id to implement.")
    implement_parser.add_argument(
        "--agent",
        required=True,
        help="Configured agent name to run.",
    )
    implement_parser.add_argument("--model", help="Optional model name passed to the agent.")
    implement_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=600.0,
        help="Hard timeout for the agent run in seconds.",
    )
    implement_parser.set_defaults(func=implement.run)

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
    complete_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the review-stage requirement.",
    )
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
    submit_review_parser.add_argument("--assignee", default="codex", help="Current implementer assignee.")
    submit_review_parser.add_argument("--reviewer", help="Reviewer assignee for this handoff.")
    submit_review_parser.set_defaults(func=handoff.run_submit_review)

    request_changes_parser = subparsers.add_parser(
        "request-changes",
        help="Return an issue to codex with requested changes.",
    )
    request_changes_parser.add_argument("id", help="Issue id to return.")
    request_changes_parser.add_argument("--notes", required=True, help="ASCII review feedback.")
    request_changes_parser.add_argument("--assignee", help="Implementation assignee to return to.")
    request_changes_parser.add_argument("--reviewer", help="Reviewer assignee returning the issue.")
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
        "--no-crlf",
        action="store_true",
        help="Disable CRLF line-ending scanning.",
    )
    check_encoding_parser.add_argument(
        "--fix",
        action="store_true",
        help="Strip leading UTF-8 BOM bytes from tracked source files.",
    )
    check_encoding_parser.set_defaults(func=check_encoding.run)

    protocol_parser = subparsers.add_parser(
        "protocol",
        help="Print the current handoff protocol.",
    )
    protocol_parser.add_argument(
        "--agent",
        help="Print the protocol for one agent (defaults to implementer flow for unknown agents).",
    )
    protocol_parser.add_argument(
        "--role",
        choices=("author", "implementer", "reviewer"),
        help="Print the protocol for a specific role instead of the agent default.",
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

    setup_parser = subparsers.add_parser(
        "setup",
        help="Initialize repo MCP handoff scaffolding and print setup diagnostics.",
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing templated files.",
    )
    setup_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    setup_parser.add_argument(
        "--check",
        action="store_true",
        help="Check setup state without writing files.",
    )
    setup_subparsers = setup_parser.add_subparsers(dest="setup_action", metavar="<action>")
    setup_check_parser = setup_subparsers.add_parser(
        "check",
        help="Check setup state without writing files.",
    )
    setup_check_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    setup_check_parser.set_defaults(func=setup.run)
    setup_apply_parser = setup_subparsers.add_parser(
        "apply",
        help="Initialize repo MCP handoff scaffolding and print setup diagnostics.",
    )
    setup_apply_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing templated files.",
    )
    setup_apply_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    setup_apply_parser.set_defaults(func=setup.run)
    setup_parser.set_defaults(func=setup.run)

    add_ref_parser = subparsers.add_parser(
        "add-ref",
        help="Register a machine-local related repository ref.",
    )
    add_ref_parser.add_argument("name", help="Short ref name.")
    add_ref_parser.add_argument("--path", required=True, help="Absolute or relative repository path.")
    add_ref_parser.add_argument(
        "--scope",
        choices=("local", "workspace"),
        default="local",
        help="Write to issuekit.local.toml or issuekit.workspace.toml.",
    )
    add_ref_parser.add_argument(
        "--path-to-workspace",
        help="Explicit workspace registry file for --scope workspace.",
    )
    add_ref_parser.set_defaults(func=propose.run_add_ref)

    list_refs_parser = subparsers.add_parser(
        "list-refs",
        help="List effective related repository refs.",
    )
    list_refs_parser.set_defaults(func=propose.run_list_refs)

    propose_parser = subparsers.add_parser(
        "propose",
        help="Send a cross-repository proposal to a related repository.",
    )
    propose_parser.add_argument("--to", help="Target related repository ref.")
    propose_parser.add_argument("--title", help="Proposal title.")
    propose_parser.add_argument("--body", help="Inline proposal body.")
    propose_parser.add_argument("--body-file", help="File containing proposal body.")
    propose_parser.add_argument("--from-issue", help="Local issue id to propose from.")
    propose_parser.add_argument("--reply", help="Local adopted issue id to reply from.")
    propose_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    propose_parser.set_defaults(func=propose.run_propose)

    incoming_parser = subparsers.add_parser(
        "incoming",
        help="List incoming cross-repository proposals.",
    )
    incoming_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    incoming_parser.set_defaults(func=propose.run_incoming)

    adopt_parser = subparsers.add_parser(
        "adopt",
        help="Adopt an incoming proposal as a local active issue.",
    )
    adopt_parser.add_argument("proposal_file", help="Incoming proposal file name or path.")
    adopt_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Priority for the adopted issue.",
    )
    adopt_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    adopt_parser.set_defaults(func=propose.run_adopt)

    discard_parser = subparsers.add_parser(
        "discard",
        help="Move an incoming proposal to incoming/discarded.",
    )
    discard_parser.add_argument("proposal_file", help="Incoming proposal file name or path.")
    discard_parser.set_defaults(func=propose.run_discard)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    return args.func(args)
