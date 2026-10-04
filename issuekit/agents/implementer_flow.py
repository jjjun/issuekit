"""Implementer run steps and gates for an already-claimed issue."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from issuekit.agentrun import AgentPrompt, AgentResult, AgentRunner
from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.parsed import parsed_is_error
from issuekit.agentrun.runner import implementation_report_instruction
from issuekit.agents import implementation_changes, implementer_report
from issuekit.agents.registry import resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.encoding import sanitize_to_ascii
from issuekit.guards.author import AuthorOrchestrationContext
from issuekit.store import managed_issue_store
from issuekit.workflow import reclaim_issue, submit_for_review


@dataclass(frozen=True)
class RunOutcome:
    """Outcome for an agent run against one claimed issue."""

    issue: Issue
    result: AgentResult
    exit_code: int
    reviewed_issue: Issue | None = None
    reason: str | None = None
    resumed_changes: bool = False


@dataclass(frozen=True)
class ImplementerRunOptions:
    agent: str
    config: IssuekitConfig
    cwd: Path
    timeout: float
    model: str | None
    reasoning_effort: str | None
    follow: bool
    prompt_suffix: str | None
    allow_no_changes: bool
    allow_author_guard_override: bool
    allow_any_branch: bool
    session: str | None
    orchestration: AuthorOrchestrationContext | None
    submit_summary: str | None
    abort_event: threading.Event | None
    reporter: Callable[[Issue, AgentResult], None] | None
    runner_factory: Callable[[], AgentRunner] | None
    store: object | None
    out: TextIO
    err: TextIO
    allow_missing_report: bool


def release_claim_after_run_error(
    issue_id: int,
    *,
    config: IssuekitConfig,
    store,
    err: TextIO,
    reason: str = "agent setup failed before execution",
) -> None:
    try:
        result = reclaim_issue(
            issue_id,
            force=True,
            reason=reason,
            config=config,
            store=store,
        )
    except Exception as exc:
        detail = " ".join(sanitize_to_ascii(str(exc)).split())
        print(f"claim_release id={issue_id} result=failed error={detail}", file=err)
    else:
        print(
            f"claim_release id={issue_id} result=reclaimed stage={result.issue.stage}",
            file=err,
        )


def implementation_prompt(
    plan_path: Path,
    *,
    changes_requested: bool = False,
) -> str:
    """Return the Issuekit implementation prompt for a claimed issue."""

    scope_instruction = (
        "The issue is back from review: address only the review feedback below, "
        "keeping the rest of the implementation as it is."
        if changes_requested
        else "Implement it fully by editing files directly in this repository."
    )
    return (
        f"Read the plan file at: {plan_path} . {scope_instruction} "
        "Do NOT run git commit or git push - "
        "leave all changes unstaged for review. Edit only code, tests, and "
        "supporting project files needed for the implementation. Write "
        "maintainable, idiomatic code that matches surrounding imports, naming, "
        "and comment density; use normal imports and real identifiers when they "
        "work. Do not split or obfuscate string literals, import paths, or "
        "identifiers, and do not use importlib/getattr/setattr/globals() "
        "indirection unless dynamic loading is truly required. Issuekit owns the "
        "API-backed issue lifecycle, including claim, submit, review, approval, "
        "and completion state; do not run issuekit claim, submit-review, "
        "request-changes, approve, or complete, and do not mutate tracker state "
        "or issue lifecycle metadata directly. If the plan is ambiguous, make "
        "the most reasonable choice and note it at the end. "
        + implementation_report_instruction(
            "the path in $ISSUEKIT_IMPLEMENTER_REPORT_FILE"
        )
    )


def resolve_ready_adapter(
    issue_id: int,
    options: ImplementerRunOptions,
    adapter: AgentAdapter | None,
) -> tuple[AgentAdapter, str | None, str | None]:
    if adapter is None:
        try:
            adapter = resolve_adapter(
                options.agent,
                config=options.config,
                model=options.model,
                reasoning_effort=options.reasoning_effort,
                role="implementer",
            )
        except (RuntimeError, ValueError):
            release_claim_after_run_error(
                issue_id,
                config=options.config,
                store=options.store,
                err=options.err,
            )
            raise

    try:
        adapter.resolve_binary()
        agent_model, agent_reasoning_effort = adapter.effective_runtime()
    except (FileNotFoundError, RuntimeError, ValueError):
        release_claim_after_run_error(
            issue_id,
            config=options.config,
            store=options.store,
            err=options.err,
        )
        raise
    return adapter, agent_model, agent_reasoning_effort


def build_prompt(issue: Issue, cwd: Path, prompt_suffix: str | None) -> AgentPrompt:
    issue_id = issue.id
    run_dir = cwd / ".agent-runs"
    plan_path = run_dir / f"issue-{issue_id}.md"
    plan_body = issue.body
    pointer = implementation_prompt(
        plan_path,
        changes_requested=bool(prompt_suffix),
    )
    if prompt_suffix:
        if plan_body:
            if not plan_body.endswith("\n"):
                plan_body += "\n"
            plan_body += "\n"
        plan_body += f"## Review feedback to address\n\n{prompt_suffix}\n"
    return AgentPrompt(
        path=plan_path,
        body=plan_body,
        pointer=pointer,
    )


def failed_run_outcome(
    issue: Issue,
    result: AgentResult,
    *,
    err: TextIO,
) -> RunOutcome | None:
    if result.timed_out:
        return RunOutcome(issue=issue, result=result, exit_code=124, reason="timed_out")
    agent_reported_error = parsed_is_error(result.parsed) is True
    if result.exit_code == 0 and not agent_reported_error:
        return None
    if implementer_report.is_startup_failure(result):
        issue_id = issue.id
        print(
            "HINT: the agent parsed a failure with no turns or token usage, "
            "which looks like a startup failure rather than an implementation "
            f"attempt. The claim on issue #{issue_id} was left in place; "
            f"re-run `issuekit implement {issue_id}` or release it with "
            f"`issuekit reclaim {issue_id}`.",
            file=err,
        )
    return RunOutcome(
        issue=issue,
        result=result,
        exit_code=result.exit_code if result.exit_code > 0 else 1,
        reason="agent_failed",
    )


def _no_changes_gate(
    issue: Issue,
    result: AgentResult,
    snapshot: implementation_changes.ImplementationChangeSnapshot,
    *,
    cwd: Path,
    allow_no_changes: bool,
    active_store,
    out: TextIO,
    err: TextIO,
) -> RunOutcome | None:
    implementation_entries = implementation_changes.implementation_entries(snapshot)
    if snapshot.root != cwd.resolve() or implementation_entries:
        return None
    issue_id = issue.id
    current_issue = active_store.get_issue(issue_id)
    if current_issue is not None and current_issue.stage == "review":
        print(
            "Issue is already at review after the agent run; treating it as submitted.",
            file=out,
        )
        return RunOutcome(
            issue=issue,
            result=result,
            exit_code=0,
            reviewed_issue=current_issue,
        )
    if not allow_no_changes:
        current_stage = current_issue.stage if current_issue is not None else "unknown"
        log_detail = implementer_report.diagnostic_log_detail(result)
        # implementation_entries is already empty here, so every
        # unfiltered entry is pre-existing.
        unattributed_entries = implementation_changes.all_implementation_entries(
            snapshot
        )
        if unattributed_entries:
            print(
                "ERROR: agent produced no implementation changes; not "
                "submitting for review. The worktree holds uncommitted "
                "modifications that were made before this run started, "
                "not by this run; this looks like a resumed run over a "
                "previous attempt's unsubmitted edits. If they are "
                "complete, submit them with --allow-no-changes. The "
                f"issue is currently at stage={current_stage}.{log_detail}",
                file=err,
            )
            return RunOutcome(
                issue=issue,
                result=result,
                exit_code=1,
                reason="no_changes",
                resumed_changes=True,
            )
        print(
            "ERROR: agent produced no implementation changes; not submitting for review. "
            f"The issue is currently at stage={current_stage}.{log_detail}",
            file=err,
        )
        return RunOutcome(issue=issue, result=result, exit_code=1, reason="no_changes")
    print(
        "No implementation changes detected; submitting for review because "
        "--allow-no-changes was set.",
        file=out,
    )
    return None


def _report_gate(
    issue: Issue,
    result: AgentResult,
    *,
    cwd: Path,
    allow_missing_report: bool,
    out: TextIO,
    err: TextIO,
) -> RunOutcome | None:
    if result.report_path is None or implementer_report.has_report_content(result.report_path):
        return None
    if not allow_missing_report:
        print(
            "ERROR: implementer report missing; not submitting for review. "
            f"Expected a report at {implementer_report.display_path(result.report_path, cwd)}. "
            "The agent must await every verification command it starts and "
            "write its closing implementation and verification report before "
            "the run ends.",
            file=err,
        )
        return RunOutcome(issue=issue, result=result, exit_code=1, reason="missing_report")
    print(
        "Implementer report missing; submitting anyway because "
        "--allow-missing-report was set.",
        file=out,
    )
    return None


def _diff_shape_gates(
    issue: Issue,
    result: AgentResult,
    snapshot: implementation_changes.ImplementationChangeSnapshot,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    err: TextIO,
) -> RunOutcome | None:
    policy = dict(config.agent_policies).get(agent)
    if policy is not None and policy.diff_shape_warn_deletions is not None:
        implementation_changes.warn_heavy_deletions(
            snapshot,
            cwd,
            deletion_threshold=policy.diff_shape_warn_deletions,
            err=err,
        )
    if policy is None or not policy.mojibake_gate:
        return None
    confirmed_hits, unconfirmed_hits = implementation_changes.mojibake_touched_hits(
        snapshot,
        cwd,
        include_halfwidth_katakana=config.gate_halfwidth_kana,
        exclude_patterns=config.check_encoding_exclude,
    )
    if not confirmed_hits and not unconfirmed_hits:
        return None
    implementation_changes.report_mojibake_gate_failure(
        confirmed_hits,
        unconfirmed_hits,
        config,
        err,
    )
    return RunOutcome(issue=issue, result=result, exit_code=1, reason="mojibake_gate")


def _submit(
    issue: Issue,
    result: AgentResult,
    snapshot: implementation_changes.ImplementationChangeSnapshot,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    active_store,
    allow_no_changes: bool,
    allow_author_guard_override: bool,
    allow_any_branch: bool,
    session: str | None,
    orchestration: AuthorOrchestrationContext | None,
    submit_summary: str | None,
    agent_model: str | None,
    agent_reasoning_effort: str | None,
) -> RunOutcome:
    implementation_entries = implementation_changes.implementation_entries(snapshot)
    reviewed_issue = submit_for_review(
        issue.id,
        summary=implementer_report.submission_summary(
            submit_summary or f"Implemented by {agent} via issuekit implement.",
            result,
            cwd,
            no_implementation_changes=allow_no_changes and not implementation_entries,
        ),
        config=config,
        store=active_store,
        cwd=cwd,
        allow_author_guard_override=allow_author_guard_override,
        allow_any_branch=allow_any_branch,
        session=session,
        orchestration=orchestration,
        agent_model=agent_model,
        agent_reasoning_effort=agent_reasoning_effort,
    )
    return RunOutcome(
        issue=issue,
        result=result,
        exit_code=0,
        reviewed_issue=reviewed_issue,
    )


def gate_and_submit(
    issue: Issue,
    result: AgentResult,
    snapshot: implementation_changes.ImplementationChangeSnapshot,
    options: ImplementerRunOptions,
    agent_model: str | None,
    agent_reasoning_effort: str | None,
) -> RunOutcome:
    if result.status_short:
        print(
            "WARNING: implementation changes are unstaged and not committed. "
            "Review the diff, then stage and commit the changes after review.",
            file=options.out,
        )

    with managed_issue_store(options.config, options.store) as active_store:
        outcome = _no_changes_gate(
            issue,
            result,
            snapshot,
            cwd=options.cwd,
            allow_no_changes=options.allow_no_changes,
            active_store=active_store,
            out=options.out,
            err=options.err,
        )
        if outcome is not None:
            return outcome
        outcome = _report_gate(
            issue,
            result,
            cwd=options.cwd,
            allow_missing_report=options.allow_missing_report,
            out=options.out,
            err=options.err,
        )
        if outcome is not None:
            return outcome
        outcome = _diff_shape_gates(
            issue,
            result,
            snapshot,
            agent=options.agent,
            config=options.config,
            cwd=options.cwd,
            err=options.err,
        )
        if outcome is not None:
            return outcome
        return _submit(
            issue,
            result,
            snapshot,
            agent=options.agent,
            config=options.config,
            cwd=options.cwd,
            active_store=active_store,
            allow_no_changes=options.allow_no_changes,
            allow_author_guard_override=options.allow_author_guard_override,
            allow_any_branch=options.allow_any_branch,
            session=options.session,
            orchestration=options.orchestration,
            submit_summary=options.submit_summary,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
        )
