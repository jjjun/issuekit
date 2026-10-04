"""Proposal-check polling for the serve command."""

from __future__ import annotations

import sys
from pathlib import Path

from issuekit.agentrun import AgentRunner
from issuekit.agents.proposal_check import run_proposal_check_cycle
from issuekit.config import IssuekitConfig
from issuekit.errors import AGENT_RUN_ERRORS

from .loop import Backoff, PollResult, ShutdownController, log_event, run_poll_loop
from .triage import retry_pending_holds


def _serve_proposal_checks_loop(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
) -> int:
    backoff = Backoff()
    answered_count = 0
    pending_holds: dict[int, tuple[str, str]] = {}

    def poll(attempt: int, backoff_seconds: float):
        nonlocal answered_count
        hold_result = retry_pending_holds(
            pending_holds,
            config=config,
            log_path=log_path,
            backoff_seconds=backoff_seconds,
        )
        if hold_result is not None:
            return hold_result
        log_event(
            sys.stderr,
            log_path,
            "proposal_checks_cycle_start",
            attempt=attempt,
            agent=agent,
            limit=args.proposal_check_limit,
        )

        try:
            decisions = run_proposal_check_cycle(
                config,
                cwd,
                agent=agent,
                timeout=float(args.timeout_sec),
                model=getattr(args, "model", None),
                reasoning_effort=getattr(args, "reasoning_effort", None),
                limit=int(args.proposal_check_limit),
                runner_factory=AgentRunner,
                log=lambda event, **fields: log_event(sys.stderr, log_path, event, **fields),
                err=sys.stderr,
                abort_event=controller.abort_event,
            )
        except AGENT_RUN_ERRORS as exc:
            log_event(
                sys.stderr,
                log_path,
                "proposal_checks_cycle_error",
                attempt=attempt,
                error=str(exc),
                backoff=backoff_seconds,
            )
            return PollResult("error", exit_code=1)

        errors = [decision for decision in decisions if decision.error is not None]
        for decision in decisions:
            if decision.hold_error and decision.hold_issue_id is not None:
                pending_holds[decision.hold_issue_id] = (
                    decision.hold_origin or "",
                    decision.hold_reason or "proposal-check approve",
                )
        if decisions:
            answered_count += sum(
                1
                for decision in decisions
                if decision.error is None
                and decision.status in {"answered", "already_decided"}
            )
            log_event(
                sys.stderr,
                log_path,
                "proposal_checks_cycle_complete",
                attempt=attempt,
                decisions=len(decisions),
                errors=len(errors),
                answered=answered_count,
            )
        else:
            return PollResult("idle")

        if errors:
            return PollResult("error", exit_code=1)

        return PollResult("success")

    return run_poll_loop(
        controller,
        backoff,
        poll=poll,
        on_idle=lambda attempt: log_event(
            sys.stderr, log_path, "proposal_checks_idle", attempt=attempt
        ),
        on_success=lambda _result, _count: None,
        on_stopped=lambda: log_event(sys.stderr, log_path, "stopped"),
        once=args.once,
        interval=float(args.interval),
        max_count=None,
        max_consecutive_failures=getattr(args, "max_run_failures", 3),
        on_failure_limit=lambda _result, failures: log_event(
            sys.stderr,
            log_path,
            "proposal_check_failure_limit",
            failures=failures,
        ),
        stop_before_retry_sleep=True,
        sleep_after_success=True,
    )
