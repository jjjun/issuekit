"""Commands for worker-side proposal-check requests."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from issuekit.commands._common import print_json
from issuekit.agents.proposal_check import (
    ProposalCheckParseError,
    ProposalCheckDecision,
    list_worker_proposal_checks,
    run_proposal_check_cycle,
)
from issuekit.agentrun import AgentRunner
from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError, resolve_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "proposal-checks",
        help="Run one worker-side proposal-check polling cycle.",
    )
    parser.add_argument("--agent", help="Configured agent name to run.")
    parser.add_argument("--model", help="Optional model name passed to the agent.")
    parser.add_argument(
        "--reasoning-effort", help="Optional reasoning effort passed to the agent."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--list",
        action="store_true",
        help="List proposal checks addressed to this worker without running an agent.",
    )
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run a single proposal-check cycle and exit (currently required).",
    )
    parser.add_argument(
        "--status",
        choices=("pending", "answered"),
        help="Filter listed proposal checks by status.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=600.0,
        help="Hard timeout for each proposal-check agent run in seconds.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum checks to list or evaluate.",
    )
    parser.add_argument("--offset", type=int, default=0, help="Checks to skip when listing.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.list:
        return _run_list(args, config)
    if not args.once:
        print(
            "proposal-checks requires either --once to run one cycle or --list to inspect checks.",
            file=sys.stderr,
        )
        return 1
    agent = resolve_implementer(args.agent, config)
    if agent is None:
        print(
            "No implementer is configured. Pass --agent, set default_implementer, "
            "or configure exactly one enabled assignee.",
            file=sys.stderr,
        )
        return 1

    def action() -> int:
        decisions = run_proposal_check_cycle(
            config,
            cwd,
            agent=agent,
            timeout=float(args.timeout_sec),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            limit=int(args.limit),
            runner_factory=AgentRunner,
            err=sys.stderr,
        )
        if args.json:
            print_json([decision.to_dict() for decision in decisions])
        else:
            _print_decisions(decisions)
        return 1 if any(decision.error for decision in decisions) else 0

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            ProposalError,
            ProposalCheckParseError,
        ),
    )


def _run_list(args, config: IssuekitConfig) -> int:
    def action() -> int:
        checks = list_worker_proposal_checks(
            config,
            status=args.status,
            limit=int(args.limit),
            offset=int(args.offset),
        )
        if args.json:
            print_json(checks)
        else:
            _print_checks(checks)
        return 0

    return run_command(action, errors=(ValueError, WorkflowError, ProposalError))


def _print_checks(checks: list[dict]) -> None:
    if not checks:
        print("No proposal checks for this worker.")
        return
    headers = (
        "id",
        "target_project",
        "proposal_id",
        "status",
        "verdict",
        "adopted_issue_ref",
        "created_at",
        "answered_at",
    )
    rows = [
        tuple(_format_check_value(check.get(header)) for header in headers)
        for check in checks
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    print(_format_row(headers, widths))
    for row in rows:
        print(_format_row(row, widths))


def _format_check_value(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(value.ljust(width) for value, width in zip(values, widths))


def _print_decisions(decisions: list[ProposalCheckDecision]) -> None:
    if not decisions:
        print("No pending proposal checks for this worker.")
        return
    for decision in decisions:
        if decision.error is not None:
            print(
                f"check={decision.check_id} proposal={decision.target_project}#{decision.proposal_id} "
                f"status=error error={decision.error}"
            )
            continue
        if decision.status == "already_decided":
            print(f"check={decision.check_id} status=already_decided")
            continue
        adopted = (
            f" adopted_issue_ref={decision.adopted_issue_ref}"
            if decision.adopted_issue_ref
            else ""
        )
        print(
            f"check={decision.check_id} proposal={decision.target_project}#{decision.proposal_id} "
            f"verdict={decision.verdict}{adopted}"
        )
