"""Implementation of the review command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from issuekit.agents.review import (
    ReviewOutcome,
    ReviewParseError,
    ReviewRunParseError,
    run_review_and_decide,
)
from issuekit.agents.runner import AgentResult, AgentRunner
from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    review_parser = subparsers.add_parser(
        "review",
        help="Drive an agent to review a review-stage issue.",
    )
    review_parser.add_argument("id", help="Issue id to review.")
    review_parser.add_argument(
        "--agent",
        required=True,
        help="Configured reviewer agent name to run.",
    )
    review_parser.add_argument("--model", help="Optional model name passed to the agent.")
    review_parser.add_argument(
        "--reasoning-effort", help="Optional reasoning effort passed to the agent."
    )
    review_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=600.0,
        help="Hard timeout for the reviewer agent run in seconds.",
    )
    review_parser.add_argument(
        "--follow",
        action="store_true",
        help="Emit a live heartbeat to stderr while the agent runs.",
    )
    review_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        cwd = Path.cwd()
        config = load_config(cwd)
        issue = get_store(config).get_issue(issue_id)
        if issue is None:
            print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
            return 1

        try:
            outcome = run_review_and_decide(
                issue,
                agent=args.agent,
                config=config,
                cwd=cwd,
                timeout=float(args.timeout_sec),
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                follow=getattr(args, "follow", False),
                runner_factory=AgentRunner,
                out=sys.stdout,
                err=sys.stderr,
            )
        except ReviewRunParseError as exc:
            _print_run_report(issue, exc.result, args.agent, decision_recorded=False)
            raise
        _print_run_report(
            issue,
            outcome.result,
            args.agent,
            decision_recorded=outcome.decided_issue is not None,
        )
        if outcome.decided_issue is not None:
            _print_decision_report(outcome)
        return outcome.exit_code

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            ReviewParseError,
        ),
    )


def _print_run_report(
    issue: Issue,
    result: AgentResult,
    agent: str,
    *,
    decision_recorded: bool,
) -> None:
    print(f"issue={issue.id} ref={issue.ref} reviewer={agent}")
    print(
        "agent_exit_code={exit_code} timed_out={timed_out} elapsed_sec={elapsed:.2f}".format(
            exit_code=result.exit_code,
            timed_out=str(result.timed_out).lower(),
            elapsed=result.elapsed_sec,
        )
    )
    print(f"stdout_log={result.stdout_path}")
    print(f"agent_log={result.agent_log_path}")
    if result.status_path:
        print(f"status_file={result.status_path}")
    if not decision_recorded:
        print("review_decision=none (no decision recorded)")


def _print_decision_report(outcome: ReviewOutcome) -> None:
    decided = outcome.decided_issue
    if decided is None:
        return
    print(
        f"review_decision verdict={outcome.verdict.verdict} "
        f"id={decided.id} ref={decided.ref} assignee={decided.assignee} "
        f"stage={decided.stage} status={decided.issue_status}"
    )
