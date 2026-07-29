"""PM request router command."""

from __future__ import annotations

import argparse
from pathlib import Path

from issuekit.agents.router import RouterParseError
from issuekit.commands._common import run_command
from issuekit.commands.request.answers import run_answer
from issuekit.commands.request.inbox import run_inbox, run_status
from issuekit.commands.request.routing import run_link, run_new_request
from issuekit.config import load_config
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    request_parser = subparsers.add_parser(
        "request",
        help="Route a PM request to owning project proposal inboxes.",
    )
    request_parser.add_argument("text", nargs="?", help="Request text or clarification answer.")
    request_parser.add_argument(
        "--answer",
        type=int,
        metavar="REQUEST_ID",
        help="Answer a pending clarification for a recorded request.",
    )
    request_parser.add_argument(
        "--status",
        nargs="?",
        const="all",
        metavar="REQUEST_ID",
        help="Show routed proposal status for one request or all requests.",
    )
    request_parser.add_argument(
        "--inbox",
        action="store_true",
        help="Show pending target clarification replies in the PM project inbox.",
    )
    request_parser.add_argument(
        "--target",
        help="Target project whose pending clarification reply is being answered.",
    )
    request_parser.add_argument(
        "--link",
        type=int,
        metavar="REQUEST_ID",
        help="Link an existing proposal ref to an unsent routed request target.",
    )
    request_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    request_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the parsed router decision without sending proposals.",
    )
    request_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=600.0,
        help="Hard timeout for the router agent run in seconds.",
    )
    request_parser.add_argument(
        "--model",
        help="Override the router agent model for this run.",
    )
    request_parser.add_argument(
        "--reasoning-effort",
        help="Override the router agent reasoning effort for this run.",
    )
    request_parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    config = load_config(cwd)

    def action() -> int:
        if args.inbox:
            if (
                args.status is not None
                or args.answer is not None
                or args.link is not None
                or args.text is not None
                or args.dry_run
            ):
                raise ValueError("--inbox cannot be combined with request text, --answer, --status, or --dry-run.")
            return run_inbox(cwd, config, json_output=args.json)
        if args.status is not None:
            if (
                args.answer is not None
                or args.link is not None
                or args.text is not None
                or args.dry_run
                or args.target
            ):
                raise ValueError("--status cannot be combined with request text, --answer, --link, --target, or --dry-run.")
            return run_status(cwd, config, request_id_arg=args.status, json_output=args.json)
        if args.link is not None:
            if args.answer is not None or args.inbox or args.dry_run:
                raise ValueError("--link cannot be combined with --answer, --inbox, or --dry-run.")
            if not args.target:
                raise ValueError("request --link requires --target.")
            if not args.text:
                raise ValueError("request --link requires a proposal ref.")
            return run_link(
                cwd,
                config,
                request_id=int(args.link),
                target_project=str(args.target),
                proposal_ref=str(args.text),
                json_output=args.json,
            )
        if args.answer is not None:
            if not args.text:
                raise ValueError("request --answer requires answer text.")
            return run_answer(
                cwd,
                config,
                request_id=int(args.answer),
                answer_text=str(args.text),
                target_project=args.target,
                json_output=args.json,
                dry_run=args.dry_run,
                timeout=float(args.timeout_sec),
                model=args.model,
                reasoning_effort=args.reasoning_effort,
            )
        if args.target:
            raise ValueError("--target can only be used with --answer or --link.")
        if not args.text:
            raise ValueError("issuekit request requires request text, --answer, --inbox, --link, or --status.")
        return run_new_request(
            cwd,
            config,
            request_text=str(args.text),
            json_output=args.json,
            dry_run=args.dry_run,
            timeout=float(args.timeout_sec),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            ProposalError,
            RouterParseError,
        ),
    )
