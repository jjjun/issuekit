"""Implementation of the implement command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from issuekit.agentrun import AgentResult, AgentRunner
from issuekit.agents.run_claimed import (
    RunOutcome,
    review_feedback_prompt,
    run_and_submit,
)
from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.guards.author import AuthorOrchestrationContext, read_author_guard
from issuekit.issues.session import new_session_token
from issuekit.store import get_store
from issuekit.workflow import WorkflowError, claim_issue, resolve_implementer

_MAPPED_ERRORS = (FileNotFoundError, RuntimeError, ValueError, TimeoutError, WorkflowError)


def register(subparsers: argparse._SubParsersAction) -> None:
    implement_parser = subparsers.add_parser(
        "implement",
        help="Drive an agent to implement an active issue.",
    )
    implement_parser.add_argument("id", help="Issue id to implement.")
    implement_parser.add_argument(
        "--agent",
        help="Configured agent name to run.",
    )
    implement_parser.add_argument("--model", help="Optional model name passed to the agent.")
    implement_parser.add_argument(
        "--reasoning-effort", help="Optional reasoning effort passed to the agent."
    )
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
    implement_parser.add_argument(
        "--allow-no-changes",
        action="store_true",
        help="Submit for review even when the agent produces no implementation diff.",
    )
    implement_parser.add_argument(
        "--allow-author-session",
        action="store_true",
        help="Override a local author-session STOP guard for human recovery.",
    )
    implement_parser.add_argument(
        "--allow-any-branch",
        action="store_true",
        help="Override the configured work_branch guard for human recovery.",
    )
    implement_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip the claim-time clean checkout and fast-forward sync guard.",
    )
    implement_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.id)

        cwd = Path.cwd()
        config = load_config(cwd)
        agent = resolve_implementer(args.agent, config)
        if agent is None:
            raise WorkflowError(
                "No implementer is configured. Pass --agent, set default_implementer, "
                "or configure exactly one enabled assignee."
            )
        issues_dir = config.issues_path(cwd)
        with get_store(config) as store:
            issue = store.get_issue(issue_id)
        if issue is None:
            print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
            return 1

        reviewer_prompt = (
            review_feedback_prompt(issue.body)
            if issue.stage == "changes_requested"
            else None
        )
        run_session = new_session_token("run")
        orchestration = AuthorOrchestrationContext(
            implementer_agent=agent,
            run_session=run_session,
        )
        claimed_issue = claim_issue(
            issue.id or issue_id,
            agent,
            config=config,
            cwd=cwd,
            allow_author_guard_override=args.allow_author_session,
            allow_any_branch=args.allow_any_branch,
            no_sync=args.no_sync,
            session=run_session,
            orchestration=orchestration,
        )
        resolved_issue_id = claimed_issue.id or issue_id
        agent_result: AgentResult | None = None

        def reporter(reported_issue: Issue, result: AgentResult) -> None:
            nonlocal agent_result
            agent_result = result
            _print_run_report(reported_issue, result, agent)

        try:
            outcome = run_and_submit(
                claimed_issue,
                agent=agent,
                config=config,
                cwd=cwd,
                issues_dir=issues_dir,
                timeout=float(args.timeout_sec),
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                follow=getattr(args, "follow", False),
                prompt_suffix=reviewer_prompt,
                allow_no_changes=getattr(args, "allow_no_changes", False),
                allow_author_guard_override=args.allow_author_session,
                allow_any_branch=args.allow_any_branch,
                session=run_session,
                orchestration=orchestration,
                submit_summary=_submit_summary(agent, cwd, config, resolved_issue_id),
                reporter=reporter,
                runner_factory=AgentRunner,
            )
        except _MAPPED_ERRORS as exc:
            reason_prefix = "submit_error" if agent_result is not None else "run_error"
            reason = f"{reason_prefix}:{str(exc).replace(chr(10), ' ')}"
            _print_terminal_lines_for_error(resolved_issue_id, agent_result, reason, config)
            raise

        if outcome.reviewed_issue is not None:
            _print_submit_report(outcome)
        _print_terminal_lines(resolved_issue_id, outcome, config)
        return outcome.exit_code

    return run_command(action, errors=_MAPPED_ERRORS)


def _print_run_report(issue: Issue, result: AgentResult, agent: str) -> None:
    print(f"issue={issue.id} ref={issue.ref} agent={agent}")
    print(
        f"agent_exit_code={result.exit_code} timed_out={str(result.timed_out).lower()} "
        f"elapsed_sec={result.elapsed_sec:.2f}"
    )
    print(f"stdout_log={result.stdout_path}")
    print(f"agent_log={result.agent_log_path}")
    if result.status_path:
        print(f"status_file={result.status_path}")
    if result.report_path and result.report_path.is_file():
        print(f"report_file={result.report_path}")
    if result.parsed:
        for key, value in sorted(result.parsed.items()):
            if key in {"stdout", "stderr"} or not value:
                continue
            print(f"{key}={value}")

    print("--- git status --short ---")
    if result.status_short:
        print(result.status_short)
    elif result.status_short == "":
        print("No changes.")
    else:
        print("Unavailable.")


def _print_submit_report(outcome: RunOutcome) -> None:
    reviewed_issue = outcome.reviewed_issue
    if reviewed_issue is None:
        return
    print(
        f"submitted_review id={reviewed_issue.id} ref={reviewed_issue.ref} "
        f"assignee={reviewed_issue.assignee} stage={reviewed_issue.stage}"
    )


def _print_terminal_lines(issue_id: int, outcome: RunOutcome, config) -> None:
    submitted = outcome.reviewed_issue is not None
    if outcome.reviewed_issue is not None:
        stage = outcome.reviewed_issue.stage
    else:
        stage = _current_stage(config, issue_id)
        print(f"not_submitted id={issue_id} stage={stage} reason={outcome.reason or 'unknown'}")
    print(
        f"post_run id={issue_id} stage={stage} submitted={str(submitted).lower()} "
        f"agent_exit={outcome.result.exit_code} cli_exit={outcome.exit_code}"
    )


def _print_terminal_lines_for_error(
    issue_id: int, agent_result: AgentResult | None, reason: str, config
) -> None:
    stage = _current_stage(config, issue_id)
    agent_exit = str(agent_result.exit_code) if agent_result is not None else "unknown"
    print(f"not_submitted id={issue_id} stage={stage} reason={reason}")
    print(f"post_run id={issue_id} stage={stage} submitted=false agent_exit={agent_exit} cli_exit=1")


def _current_stage(config, issue_id: int) -> str:
    try:
        with get_store(config) as store:
            current_issue = store.get_issue(issue_id)
    except _MAPPED_ERRORS:
        return "unknown"
    return current_issue.stage if current_issue is not None else "unknown"


def _submit_summary(agent: str, cwd: Path, config, issue_id: int) -> str:
    orchestrator = _orchestrator_identity(cwd, config, issue_id)
    return f"Implemented by {agent} via issuekit implement (orchestrated by {orchestrator})."


def _orchestrator_identity(cwd: Path, config, issue_id: int) -> str:
    guard = read_author_guard(cwd)
    if (
        guard is not None
        and guard.project == config.project
        and _guard_targets_issue(guard, config, issue_id)
    ):
        return f"{guard.author_agent}@{guard.worker or 'unregistered-worker'}"
    worker = config.worker_key()
    return f"issuekit@{worker or 'unregistered-worker'}"


def _guard_targets_issue(guard, config, issue_id: int) -> bool:
    if guard.kind != "issue":
        return False
    if guard.id:
        return guard.id == str(issue_id)
    return guard.ref == f"{config.project}#{issue_id}"
