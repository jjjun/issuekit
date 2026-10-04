"""Implementation of the review command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.agentrun import AgentResult, AgentRunner
from issuekit.agents.review import (
    ReviewOutcome,
    ReviewRunParseError,
    run_review_and_decide,
)
from issuekit.commands._common import add_agent_run_options, add_follow_flag, run_agent_command
from issuekit.config import load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.errors import AGENT_RUN_ERRORS
from issuekit.inputs import active_issue_not_found
from issuekit.store import get_store


def register(subparsers: argparse._SubParsersAction) -> None:
    review_parser = subparsers.add_parser(
        "review",
        help="Drive an agent to review and record a decision on a review-stage issue.",
        description=(
            "Drive an agent to review and record a decision on a review-stage issue."
        ),
    )
    review_parser.add_argument("id", help="Issue id to review.")
    add_agent_run_options(
        review_parser,
        agent_help="Configured reviewer agent name to run.",
        agent_required=True,
        model_help="Optional model name passed to the agent.",
        effort_help="Optional reasoning effort passed to the agent.",
        timeout_default=600.0,
        timeout_help="Hard timeout for the reviewer agent run in seconds.",
    )
    add_follow_flag(
        review_parser,
        help=(
            "Emit a live heartbeat to stderr; it polls git status read-only without an"
            " index lock and is safe for issues that rewrite the checkout."
        ),
    )
    review_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)
        cwd = Path.cwd()
        config = load_config(cwd)
        with get_store(config) as store:
            issue = store.get_issue(issue_id)
        if issue is None:
            print(active_issue_not_found(issue_id), file=sys.stderr)
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
            _print_run_report(
                issue,
                exc.result,
                args.agent,
                decision_recorded=False,
                decision_discarded=True,
                discard_reason=str(exc),
            )
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

    return run_agent_command(
        action,
        errors=AGENT_RUN_ERRORS,
    )


def _print_run_report(
    issue: Issue,
    result: AgentResult,
    agent: str,
    *,
    decision_recorded: bool,
    decision_discarded: bool = False,
    discard_reason: str | None = None,
) -> None:
    print(f"issue={issue.id} ref={issue.ref} reviewer={agent}")
    print(
        f"agent_exit_code={result.exit_code} timed_out={str(result.timed_out).lower()} "
        f"elapsed_sec={result.elapsed_sec:.2f}"
    )
    print(f"stdout_log={result.stdout_path}")
    print(f"agent_log={result.agent_log_path}")
    if result.status_path:
        print(f"status_file={result.status_path}")
    if decision_discarded:
        print(
            "review_decision=discarded "
            f"(unparseable review block: {discard_reason or 'unknown parse error'})"
        )
        print(
            "manual_fallback="
            f"issuekit request-changes {issue.id} --notes <text> OR "
            f"issuekit approve {issue.id} --verification <text>"
        )
    elif not decision_recorded:
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
