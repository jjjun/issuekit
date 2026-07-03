"""Commands for worker-side proposal-check requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from issuekit.agents.proposal_check import (
    ProposalCheckParseError,
    ProposalCheckDecision,
    run_proposal_check_cycle,
)
from issuekit.agents.runner import AgentRunner
from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "proposal-checks",
        help="Run one worker-side proposal-check polling cycle.",
    )
    parser.add_argument("--agent", help="Configured agent name to run.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single proposal-check cycle and exit (currently required).",
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
        help="Maximum pending checks to evaluate in this cycle.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not args.once:
        print(
            "proposal-checks currently supports only --once; pass --once to run one cycle.",
            file=sys.stderr,
        )
        return 1
    agent = _resolve_agent(args.agent, config)
    if agent is None:
        print(
            "--agent is required unless exactly one assignee is configured.",
            file=sys.stderr,
        )
        return 1

    def action() -> int:
        decisions = run_proposal_check_cycle(
            config,
            cwd,
            agent=agent,
            timeout=float(args.timeout_sec),
            limit=int(args.limit),
            runner_factory=AgentRunner,
            out=sys.stdout,
            err=sys.stderr,
        )
        if args.json:
            print(json.dumps([decision.to_dict() for decision in decisions], indent=2))
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


def _resolve_agent(agent: str | None, config: IssuekitConfig) -> str | None:
    if agent:
        return agent
    if len(config.assignees) == 1:
        return config.assignees[0]
    return None


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
