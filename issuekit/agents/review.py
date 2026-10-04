"""Run a reviewer agent and apply its structured verdict."""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from issuekit.agentrun import AgentResult, AgentRunner
from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.parsed import parsed_is_error
from issuekit.agents.readonly import (
    ReadonlyAgentRun,
    prompt_from_spec,
    repository_mutation_message,
    run_readonly_evaluation,
    stdout_text,
)
from issuekit.agents.registry import resolve_adapter
from issuekit.agents.review_context import (
    ReviewDiffContext,
    _collect_git_diff_context,
    _latest_handoff_has_run_log,
    _render_review_prompt,
)
from issuekit.agents.review_output import (
    ReviewRunParseError,
    ReviewVerdict,
    _empty_verdict,
    parse_review_output,
)
from issuekit.config import IssuekitConfig
from issuekit.core import Issue, worker_keys_match
from issuekit.errors import WorkflowError
from issuekit.issues.service import approve_issue
from issuekit.prompts import REVIEW_PROMPT, ReviewParseError
from issuekit.store import managed_issue_store
from issuekit.workflow import ensure_assigned_reviewer, request_changes


@dataclass(frozen=True)
class ReviewOutcome:
    issue: Issue
    result: AgentResult
    verdict: ReviewVerdict
    exit_code: int
    decided_issue: Issue | None = None


def run_review_and_decide(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    timeout: float,
    model: str | None = None,
    reasoning_effort: str | None = None,
    follow: bool = False,
    abort_event: threading.Event | None = None,
    runner_factory=None,
    store=None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> ReviewOutcome:
    """Run an agent against a review-stage issue and apply its verdict."""

    out = out or sys.stdout
    err = err or sys.stderr
    issue_id = _require_reviewable(issue, agent=agent, config=config)

    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
        role="reviewer",
    )
    diff_context = _collect_git_diff_context(cwd, issue=issue)
    _require_review_evidence(issue, diff_context)
    run = _run_reviewer(
        issue, issue_id=issue_id, agent=agent, cwd=cwd, timeout=timeout,
        adapter=adapter, diff_context=diff_context, follow=follow,
        abort_event=abort_event, runner_factory=runner_factory,
    )
    failed_outcome = _failed_run_outcome(run, issue, err)
    if failed_outcome is not None:
        return failed_outcome
    return _apply_verdict(
        run, issue, issue_id=issue_id, agent=agent, adapter=adapter,
        config=config, store=store, out=out, err=err,
    )


def _require_reviewable(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
) -> int:
    issue_id = issue.id
    if issue_id is None:
        raise ValueError("Review issue is missing an id.")
    if issue.stage != "review":
        raise WorkflowError(f"Issue #{issue_id} is not at the review stage.")
    _ensure_registered_distinct_worker(issue, agent=agent, config=config)
    ensure_assigned_reviewer(issue, agent, agent)
    return issue_id


def _require_review_evidence(
    issue: Issue,
    diff_context: ReviewDiffContext,
) -> None:
    if not diff_context.has_changed_files and _latest_handoff_has_run_log(issue):
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by an agent run whose changes are not "
            "in this checkout. Run the review in the implementing checkout, or commit "
            "and push the changes first."
        )
    if not diff_context.has_changed_files and not diff_context.has_handoff_evidence:
        raise WorkflowError(
            "No implementation diff is available for automated review; "
            "refusing to run the reviewer agent."
        )


def _run_reviewer(
    issue: Issue,
    *,
    issue_id: int,
    agent: str,
    cwd: Path,
    timeout: float,
    adapter: AgentAdapter,
    diff_context: ReviewDiffContext,
    follow: bool,
    abort_event: threading.Event | None,
    runner_factory,
) -> ReadonlyAgentRun:
    return run_readonly_evaluation(
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory or AgentRunner,
        prompt=prompt_from_spec(
            REVIEW_PROMPT,
            cwd=cwd,
            filename=f"review-issue-{issue_id}.md",
            body=_render_review_prompt(issue, diff_context=diff_context),
        ),
        label="Reviewer",
        subject=f"issue #{issue_id}",
        issue_id=issue_id,
        follow=follow,
        abort_event=abort_event,
    )


def _failed_run_outcome(
    run: ReadonlyAgentRun,
    issue: Issue,
    err: TextIO,
) -> ReviewOutcome | None:
    result = run.result
    if run.repository_modified:
        print(
            repository_mutation_message(
                "ERROR: reviewer run modified repository state; "
                "not applying review verdict.",
                run,
            ),
            file=err,
        )
        if run.repository_error:
            print(f"ERROR: {run.repository_error}", file=err)
    if result.timed_out:
        return ReviewOutcome(issue=issue, result=result, verdict=_empty_verdict(), exit_code=124)
    if result.exit_code != 0 or parsed_is_error(result.parsed) is True:
        return ReviewOutcome(
            issue=issue,
            result=result,
            verdict=_empty_verdict(),
            exit_code=result.exit_code if result.exit_code > 0 else 1,
        )
    if run.repository_modified:
        return ReviewOutcome(issue=issue, result=result, verdict=_empty_verdict(), exit_code=1)
    return None


def _apply_verdict(
    run: ReadonlyAgentRun,
    issue: Issue,
    *,
    agent: str,
    adapter: AgentAdapter,
    config: IssuekitConfig,
    issue_id: int,
    store,
    out: TextIO,
    err: TextIO,
) -> ReviewOutcome:
    try:
        verdict = parse_review_output(stdout_text(run.result), err=err)
    except ReviewParseError as exc:
        raise ReviewRunParseError(exc, run.result) from exc

    with managed_issue_store(config, store) as active_store:
        agent_model, agent_reasoning_effort = adapter.effective_runtime()
        if verdict.verdict == "approve":
            decided = approve_issue(
                issue_id,
                summary="Approved by reviewer agent.",
                verification=verdict.verification,
                reviewer=agent,
                config=config,
                store=active_store,
                agent_model=agent_model,
                agent_reasoning_effort=agent_reasoning_effort,
            )
            print(f"approved id={decided.id} ref={decided.ref}", file=out)
        else:
            decided = request_changes(
                issue_id,
                notes=verdict.notes,
                reviewer=agent,
                config=config,
                store=active_store,
                agent_model=agent_model,
                agent_reasoning_effort=agent_reasoning_effort,
            )
            print(
                f"requested_changes id={decided.id} ref={decided.ref} "
                f"assignee={decided.assignee} stage={decided.stage}",
                file=out,
            )
        return ReviewOutcome(
            issue=issue,
            result=run.result,
            verdict=verdict,
            exit_code=0,
            decided_issue=decided,
        )


def _ensure_registered_distinct_worker(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
) -> None:
    reviewer_worker = config.qualified_worker_key()
    if reviewer_worker is None:
        raise WorkflowError(
            "Automated review requires a registered worker identity. Run `issuekit add` first."
        )
    if issue.worker and worker_keys_match(issue.worker, reviewer_worker):
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by worker {issue.worker}; "
            "self-review by the same worker is not allowed."
        )
    if not issue.worker and issue.implementer == agent:
        no_eligible_reviewer = not any(
            configured_agent != issue.implementer
            for configured_agent, _run_config in config.agents
        )
        no_eligible_reviewer_message = (
            " no eligible reviewer via --agent: "
            f"{agent} is the implementer and no other agent is configured; "
            "use the open review pool (issuekit serve --review) or configure another reviewer."
            if no_eligible_reviewer
            else ""
        )
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by {agent}; self-review is not allowed."
            f"{no_eligible_reviewer_message}"
        )
