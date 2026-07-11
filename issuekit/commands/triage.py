"""Implementation of the one-shot triage-author command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from issuekit.agents.runner import AgentRunner
from issuekit.agents.triage_author import (
    TriageAuthorParseError,
    TriageDecision,
    run_triage_author_cycle,
)
from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    triage_parser = subparsers.add_parser(
        "triage",
        help="Run one agent-backed triage-author cycle over the proposal inbox.",
    )
    triage_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single evaluation cycle and exit (currently required).",
    )
    triage_parser.add_argument("--model", help="Optional model name passed to the agent.")
    triage_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=600.0,
        help="Hard timeout for each triage agent run in seconds.",
    )
    triage_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    triage_parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    config = load_config(cwd)
    if not args.once:
        print(
            "triage currently supports only --once; pass --once to run one cycle.",
            file=sys.stderr,
        )
        return 1
    if not config.triage.author_agent:
        print(
            "triage --once requires [tool.issuekit.triage] author_agent to be set.",
            file=sys.stderr,
        )
        return 1

    def action() -> int:
        decisions = run_triage_author_cycle(
            config,
            cwd,
            timeout=float(args.timeout_sec),
            model=args.model,
            runner_factory=AgentRunner,
            out=sys.stdout,
            err=sys.stderr,
        )
        if args.json:
            print(json.dumps([decision.to_dict() for decision in decisions], indent=2))
        else:
            _print_decisions(decisions)
        return 0

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            ProposalError,
            TriageAuthorParseError,
        ),
    )


def _print_decisions(decisions: list[TriageDecision]) -> None:
    if not decisions:
        print("No pending proposals matched triage policy.")
        return
    for decision in decisions:
        if decision.error is not None:
            print(
                f"proposal={decision.proposal_id} decision={decision.decision} "
                f"error={decision.error}"
            )
        elif decision.decision == "adopt":
            print(
                f"proposal={decision.proposal_id} decision=adopt "
                f"issue={decision.detail or decision.issue_id}"
            )
        else:
            print(
                f"proposal={decision.proposal_id} decision={decision.decision} "
                f"detail={decision.detail}"
            )
