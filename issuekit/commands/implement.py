"""Implementation of the implement command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.agents.run_claimed import (
    RunOutcome,
    review_feedback_prompt,
    run_and_submit,
)
from issuekit.agents.runner import AgentResult, AgentRunner
from issuekit.config import load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.store import get_store
from issuekit.workflow import WorkflowError, claim_issue


def run(args) -> int:
    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    cwd = Path.cwd()
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)
    try:
        issue = get_store(config, issues_dir).get_issue(issue_id)
    except (WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if issue is None:
        print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
        return 1
    if issue.decode_error:
        print(
            f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}",
            file=sys.stderr,
        )
        return 1

    reviewer_prompt = (
        review_feedback_prompt(issue.frontmatter.body)
        if issue.stage == "changes_requested"
        else None
    )
    try:
        claimed_issue = claim_issue(issues_dir, issue.id or issue_id, args.agent, config=config)
        outcome = run_and_submit(
            claimed_issue,
            agent=args.agent,
            config=config,
            cwd=cwd,
            issues_dir=issues_dir,
            timeout=float(args.timeout_sec),
            model=args.model,
            follow=getattr(args, "follow", False),
            prompt_suffix=reviewer_prompt,
            reporter=lambda issue, result: _print_run_report(issue, result, args.agent),
            runner_factory=AgentRunner,
        )
    except (FileNotFoundError, RuntimeError, ValueError, TimeoutError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if outcome.reviewed_issue is not None:
        _print_submit_report(outcome)
    return outcome.exit_code


def _print_run_report(issue: Issue, result: AgentResult, agent: str) -> None:
    print(f"issue={issue.id} file={issue.relative_path} agent={agent}")
    print(
        "exit_code={exit_code} timed_out={timed_out} elapsed_sec={elapsed:.2f}".format(
            exit_code=result.exit_code,
            timed_out=str(result.timed_out).lower(),
            elapsed=result.elapsed_sec,
        )
    )
    print(f"stdout_log={result.stdout_path}")
    print(f"agent_log={result.agent_log_path}")
    if result.status_path:
        print(f"status_file={result.status_path}")
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
        f"submitted_review id={reviewed_issue.id} file={reviewed_issue.relative_path} "
        f"assignee={reviewed_issue.assignee} stage={reviewed_issue.stage}"
    )
