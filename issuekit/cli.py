"""Command-line dispatcher for issuekit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from issuekit.commands import (
    add,
    approve,
    auth,
    author,
    check_encoding,
    claim,
    complete,
    handoff,
    implement,
    info,
    init,
    migrate_to_api,
    negotiate,
    propose,
    protocol,
    queue,
    runs,
    serve,
    setup,
    validate,
)


COMMANDS = (
    "info",
    "add",
    "register",
    "login",
    "logout",
    "author",
    "implement",
    "negotiate",
    "threads",
    "validate",
    "migrate-to-api",
    "migrate-proposals-to-api",
    "complete",
    "approve",
    "claim",
    "submit-review",
    "request-changes",
    "queue",
    "runs",
    "serve",
    "check-encoding",
    "protocol",
    "init",
    "setup",
    "add-ref",
    "list-refs",
    "propose",
    "incoming",
    "outgoing",
    "adopt",
    "discard",
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

    info_parser = subparsers.add_parser("info", help="Show issue tracker status.")
    info_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    info_parser.set_defaults(func=info.run)

    add_parser = subparsers.add_parser(
        "add",
        aliases=("register",),
        help="Register this checkout as a local worker.",
    )
    add_parser.add_argument("--machine-id", help="Override the hostname-derived machine id.")
    add_parser.add_argument("--repo-id", help="Override the git-origin-derived repository id.")
    add_parser.add_argument("--worker-id", help="Override the checkout worker id.")
    add_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing pinned worker id or local collision.",
    )
    add_parser.set_defaults(func=add.run)

    login_parser = subparsers.add_parser(
        "login",
        help="Authenticate to the API and cache the access token.",
    )
    login_parser.add_argument("--user", help="API username (defaults to ISSUEKIT_API_USER).")
    login_parser.set_defaults(func=auth.run_login)

    logout_parser = subparsers.add_parser(
        "logout",
        help="Log out of the API and remove the cached access token.",
    )
    logout_parser.set_defaults(func=auth.run_logout)

    author_parser = subparsers.add_parser(
        "author",
        help="Create an active issue authored by an agent.",
    )
    author_parser.add_argument("--title", required=True, help="Issue title.")
    body_group = author_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Inline issue body.")
    body_group.add_argument("--body-file", help="File containing the issue body.")
    author_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Issue priority.",
    )
    author_parser.add_argument("--agent", required=True, help="Configured author agent.")
    author_parser.add_argument("--assign", help="Optional implementer assignee.")
    author_parser.set_defaults(func=author.run)

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
    implement_parser.add_argument(
        "--follow",
        action="store_true",
        help="Emit a live heartbeat to stderr while the agent runs.",
    )
    implement_parser.set_defaults(func=implement.run)

    negotiate_parser = subparsers.add_parser(
        "negotiate",
        help="Drive a bounded cross-repository design negotiation.",
    )
    negotiate_parser.add_argument("--from-issue", help="Originating issue id.")
    negotiate_parser.add_argument("--to", help="Target project name.")
    negotiate_parser.add_argument(
        "--finalize",
        metavar="THREAD_ID",
        help="Create cross-linked implementation issues for an agreed thread.",
    )
    negotiate_parser.add_argument(
        "--frontend-agent",
        help="Configured agent representing the frontend side.",
    )
    negotiate_parser.add_argument(
        "--backend-agent",
        help="Configured agent representing the backend side.",
    )
    negotiate_parser.add_argument(
        "--max-rounds",
        type=int,
        default=negotiate.DEFAULT_MAX_ROUNDS,
        help="Maximum total agent turns, including the opening turn.",
    )
    negotiate_parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the local mock negotiation store.",
    )
    negotiate_parser.add_argument("--model", help="Optional model name passed to both agents.")
    negotiate_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=120.0,
        help="Hard timeout for each negotiation turn in seconds.",
    )
    negotiate_parser.add_argument(
        "--author-agent",
        default="codex",
        help="Author agent for issues created by --finalize.",
    )
    negotiate_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Priority for issues created by --finalize.",
    )
    negotiate_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    negotiate_parser.set_defaults(func=negotiate.run)

    threads_parser = subparsers.add_parser(
        "threads",
        help="Inspect negotiation thread status.",
    )
    threads_parser.add_argument("thread_id", nargs="?", help="Negotiation thread id to inspect.")
    threads_parser.add_argument(
        "--status",
        choices=("negotiating", "agreed", "blocked"),
        help="Filter listed threads by status.",
    )
    threads_parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the local mock negotiation store.",
    )
    threads_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    threads_parser.set_defaults(func=negotiate.run_threads)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate API connectivity and issue response shape.",
    )
    validate_parser.set_defaults(func=validate.run)

    migrate_parser = subparsers.add_parser(
        "migrate-to-api",
        help="Import legacy docs/issues issue files into the API backend.",
    )
    migrate_parser.add_argument(
        "--issues-dir",
        help="Legacy issue directory to import (defaults to configured issues_dir).",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate the import payload without posting it.",
    )
    migrate_parser.set_defaults(func=migrate_to_api.run)

    migrate_proposals_parser = subparsers.add_parser(
        "migrate-proposals-to-api",
        help="Import legacy docs/issues incoming proposal files into the API backend.",
    )
    migrate_proposals_parser.add_argument(
        "--issues-dir",
        help="Legacy issue directory containing incoming proposals (defaults to configured issues_dir).",
    )
    migrate_proposals_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate the proposal import payload without posting it.",
    )
    migrate_proposals_parser.set_defaults(func=migrate_to_api.run_proposals)

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

    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve a review-stage issue.",
    )
    approve_parser.add_argument("id", help="Issue id to approve.")
    approve_parser.add_argument("--verification", required=True, help="Verification notes.")
    approve_parser.add_argument("--summary", help="Approval summary.")
    approve_parser.add_argument("--reviewer", help="Reviewer approving this issue.")
    approve_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the review-stage requirement.",
    )
    approve_parser.set_defaults(func=approve.run)

    claim_parser = subparsers.add_parser(
        "claim",
        help="Claim an issue for an assignee; defaults to the next eligible issue.",
    )
    claim_parser.add_argument("--id", help="Specific issue id to claim.")
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

    runs_parser = subparsers.add_parser(
        "runs",
        help="List and inspect agent runs.",
    )
    runs_parser.add_argument("run_id", nargs="?", help="Run id to inspect.")
    runs_parser.add_argument(
        "--active",
        action="store_true",
        help="Show only running agent runs.",
    )
    runs_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    runs_parser.set_defaults(func=runs.run)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Poll for eligible issues and run this checkout's worker agent.",
    )
    serve_parser.add_argument("--agent", help="Configured agent name to run.")
    serve_parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="Idle poll interval in seconds.",
    )
    serve_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        help="Priority filter for claim-next.",
    )
    serve_parser.add_argument(
        "--once",
        action="store_true",
        help="Attempt at most one claim and then exit.",
    )
    serve_parser.add_argument(
        "--max-issues",
        type=int,
        help="Exit after this many successful submissions.",
    )
    serve_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=1800.0,
        help="Hard timeout for each agent run in seconds.",
    )
    serve_parser.set_defaults(func=serve.run)

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
        choices=("author", "implementer", "reviewer", "triage"),
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

    outgoing_parser = subparsers.add_parser(
        "outgoing",
        help="List proposals this project sent to a target project's inbox.",
    )
    outgoing_parser.add_argument(
        "--to",
        required=True,
        help="Target project whose inbox holds the outgoing proposals.",
    )
    outgoing_parser.add_argument("--id", type=int, help="Look up a single proposal id.")
    outgoing_parser.add_argument(
        "--status",
        help="Filter by proposal status (pending, adopted, or discarded).",
    )
    outgoing_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    outgoing_parser.set_defaults(func=propose.run_outgoing)

    adopt_parser = subparsers.add_parser(
        "adopt",
        help="Adopt an incoming proposal as a local active issue.",
    )
    adopt_parser.add_argument("proposal", help="Proposal id.")
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
        help="Discard an incoming proposal.",
    )
    discard_parser.add_argument("proposal", help="Proposal id.")
    discard_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    discard_parser.set_defaults(func=propose.run_discard)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    return args.func(args)
