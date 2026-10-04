"""Proposal triage steps shared by serve polling modes."""

from __future__ import annotations

import sys
from pathlib import Path

from issuekit.agents.triage_author import TriageDecision, run_triage_author_cycle
from issuekit.commands.serve.loop import (
    PollResult,
    ShutdownController,
    log_event,
    should_recreate_store,
)
from issuekit.config import IssuekitConfig
from issuekit.errors import WorkflowError
from issuekit.proposals import ProposalError
from issuekit.proposals.adopt import (
    AdoptedIssueHoldError,
    auto_adopt_incoming_proposals,
    hold_adopted_issue,
)


def retry_pending_holds(
    pending_holds: dict[int, tuple[str, str]],
    *,
    config: IssuekitConfig,
    log_path: Path,
    backoff_seconds: float,
) -> PollResult | None:
    for issue_id, (origin, reason) in list(pending_holds.items()):
        try:
            hold_adopted_issue(
                config,
                issue_id,
                origin=origin,
                reason=reason,
            )
        except AdoptedIssueHoldError as exc:
            log_event(
                sys.stderr,
                log_path,
                "hold_error",
                issue=issue_id,
                error=str(exc),
                backoff=backoff_seconds,
            )
            return PollResult(
                status="error",
                exit_code=1,
                recreate_store=should_recreate_store(exc),
            )
        del pending_holds[issue_id]
        log_event(
            sys.stderr,
            log_path,
            "held_for_release",
            issue=issue_id,
            origin=origin,
            reason=reason,
        )
    return None

def _triage_enabled(args, config: IssuekitConfig) -> bool:
    return bool(getattr(args, "triage", False) or config.triage.auto_adopt)

def _run_triage_author_cycle(
    args,
    *,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
) -> list[TriageDecision]:
    def emit(event: str, **fields: object) -> None:
        log_event(sys.stderr, log_path, event, **fields)

    return run_triage_author_cycle(
        config,
        cwd,
        timeout=float(args.timeout_sec),
        model=getattr(args, "model", None),
        reasoning_effort=getattr(args, "reasoning_effort", None),
        log=emit,
        err=sys.stderr,
        abort_event=controller.abort_event,
    )


def run_triage_step(poller, backoff_seconds: float) -> PollResult | None:
    if not _triage_enabled(poller.args, poller.config):
        return None
    try:
        if poller.config.triage.author_agent:
            decisions = _run_triage_author_cycle(
                poller.args,
                config=poller.config,
                cwd=poller.cwd,
                log_path=poller.log_path,
                controller=poller.controller,
            )
            hold_failed = False
            for decision in decisions:
                if decision.held:
                    log_event(
                        sys.stderr,
                        poller.log_path,
                        "held_for_release",
                        issue=decision.issue_id,
                        origin=decision.origin,
                        reason="triage author",
                    )
                if decision.hold_error and decision.issue_id is not None:
                    poller.pending_holds[decision.issue_id] = (
                        decision.origin,
                        "triage author",
                    )
                    hold_failed = True
                    log_event(
                        sys.stderr,
                        poller.log_path,
                        "hold_error",
                        issue=decision.issue_id,
                        error=decision.error,
                        backoff=backoff_seconds,
                    )
            if hold_failed:
                return PollResult(status="error", exit_code=1)
        else:
            def log_adoption_error(
                proposal_id: object,
                error: Exception,
            ) -> None:
                log_event(
                    sys.stderr,
                    poller.log_path,
                    "triage_adoption_error",
                    proposal=proposal_id,
                    error=str(error),
                    backoff=backoff_seconds,
                )

            hold_failed = False
            for outcome in auto_adopt_incoming_proposals(
                poller.config,
                on_adoption_error=log_adoption_error,
            ):
                log_event(
                    sys.stderr,
                    poller.log_path,
                    "auto_adopted",
                    proposal=outcome.get("proposal_id"),
                    issue=outcome.get("issue_id"),
                    priority=poller.config.triage.default_priority,
                )
                if outcome.get("held"):
                    log_event(
                        sys.stderr,
                        poller.log_path,
                        "held_for_release",
                        issue=outcome.get("issue_id"),
                        proposal=outcome.get("proposal_id"),
                        origin=outcome.get("hold_origin") or outcome.get("origin"),
                        reason="serve auto-adopt",
                    )
                if outcome.get("hold_error"):
                    issue_id = outcome.get("issue_id")
                    if issue_id is not None:
                        origin = str(outcome.get("hold_origin", ""))
                        reason = str(outcome.get("hold_reason", "serve auto-adopt"))
                        poller.pending_holds[int(issue_id)] = (origin, reason)
                    hold_failed = True
                    log_event(
                        sys.stderr,
                        poller.log_path,
                        "hold_error",
                        issue=outcome.get("issue_id"),
                        proposal=outcome.get("proposal_id"),
                        error=outcome.get("hold_error"),
                        backoff=backoff_seconds,
                    )
            if hold_failed:
                return PollResult(status="error", exit_code=1)
    except (ProposalError, TimeoutError, WorkflowError, ValueError) as exc:
        log_event(
            sys.stderr,
            poller.log_path,
            "triage_error",
            error=str(exc),
            backoff=backoff_seconds,
        )
        return PollResult(
            status="error",
            exit_code=1,
            recreate_store=should_recreate_store(exc),
        )
    return None
