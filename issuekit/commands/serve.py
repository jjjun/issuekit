"""Implementation of the serve command."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from issuekit.agentrun import AgentRunner
from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.run_dir import ServeLockError, prepare_run_dir
from issuekit.agentrun.run_dir import serve_lock as _serve_lock
from issuekit.agents.proposal_check import (
    run_proposal_check_cycle,
)
from issuekit.agents.run_claimed import _release_claim_after_run_error, preflight_agent
from issuekit.agents.triage_author import TriageDecision, run_triage_author_cycle
from issuekit.commands._common import (
    add_agent_option,
    add_guard_override_flags,
    add_model_options,
    add_timeout_option,
)
from issuekit.commands._heartbeat import warn_if_staleness_not_wider
from issuekit.commands.serve_loop import (
    Backoff,
    PollResult,
    ShutdownController,
    run_poll_loop,
)
from issuekit.commands.serve_loop import (
    close_store as _close_store,
)
from issuekit.commands.serve_loop import (
    find_implementing_issues as _find_implementing_issues,
)
from issuekit.commands.serve_loop import log_event as _log
from issuekit.commands.serve_loop import (
    recreate_store as _recreate_store,
)
from issuekit.commands.serve_loop import (
    run_claimed_issue as _run_claimed_issue,
)
from issuekit.commands.serve_loop import (
    run_review_issue as _run_review_issue,
)
from issuekit.commands.serve_loop import (
    should_recreate_store as _should_recreate_store,
)
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue
from issuekit.errors import AGENT_RUN_ERRORS, WorkflowError
from issuekit.issues.orphans import DEFAULT_STALE_AFTER_SEC
from issuekit.proposals import ProposalError
from issuekit.proposals.adopt import (
    AdoptedIssueHoldError,
    auto_adopt_incoming_proposals,
    hold_adopted_issue,
)
from issuekit.signals import installed_signal_handlers
from issuekit.store import get_store
from issuekit.workers.registration import WorkerHeartbeat
from issuekit.workflow import claim_next, next_review, require_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    serve_parser = subparsers.add_parser(
        "serve",
        help="Poll for eligible issues and run this checkout's worker agent.",
    )
    add_agent_option(serve_parser, help="Configured agent name to run.")
    add_model_options(
        serve_parser,
        model_help=(
            "Optional model name applied to every agent launched by this serve loop; "
            "use per-agent config for mixed-agent model selection."
        ),
        effort_help=(
            "Optional reasoning effort applied to every agent launched by this serve "
            "loop; use per-agent config for mixed-agent effort selection."
        ),
    )
    serve_parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="Idle poll interval in seconds.",
    )
    serve_parser.add_argument(
        "--heartbeat-interval",
        type=float,
        help=(
            "Worker heartbeat interval in seconds; overrides "
            "worker_heartbeat_interval_sec."
        ),
    )
    serve_parser.add_argument(
        "--max-heartbeat-failures",
        type=int,
        default=0,
        help="Stop gracefully after this many consecutive heartbeat failures (default: 0, unlimited).",
    )
    serve_parser.add_argument(
        "--max-run-failures",
        type=int,
        default=3,
        help="Stop after this many consecutive failed runs (default: 3, 0: unlimited).",
    )
    serve_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        help="Priority filter for claim-next.",
    )
    serve_parser.add_argument(
        "--once",
        action="store_true",
        help="Attempt at most one claim and then exit.",
    )
    serve_parser.add_argument(
        "--triage",
        action="store_true",
        help="Auto-adopt matching incoming proposals before each claim attempt.",
    )
    serve_mode = serve_parser.add_mutually_exclusive_group()
    serve_mode.add_argument(
        "--review",
        action="store_true",
        help="Poll the review pool and run this checkout's reviewer agent.",
    )
    serve_mode.add_argument(
        "--proposal-checks",
        action="store_true",
        help="Poll pending proposal checks addressed to this worker.",
    )
    serve_parser.add_argument(
        "--proposal-check-limit",
        type=int,
        default=50,
        help="Maximum proposal checks to evaluate per polling cycle.",
    )
    serve_parser.add_argument(
        "--max-issues",
        type=int,
        help="Exit after this many successful submissions.",
    )
    add_timeout_option(
        serve_parser,
        default=1800.0,
        help="Hard timeout for each agent run in seconds.",
    )
    add_guard_override_flags(
        serve_parser,
        author_session=False,
    )
    serve_parser.set_defaults(func=run)


class ServeMode(StrEnum):
    IMPLEMENT = "implement"
    REVIEW = "review"
    PROPOSAL_CHECKS = "proposal_checks"


_MODE_OPTIONS = {
    ServeMode.REVIEW: "--review",
    ServeMode.PROPOSAL_CHECKS: "--proposal-checks",
}

_MODE_REJECTED_OPTIONS = {
    ServeMode.IMPLEMENT: {},
    ServeMode.REVIEW: {
        "triage": "--triage",
        "priority": "--priority",
    },
    ServeMode.PROPOSAL_CHECKS: {
        "triage": "--triage",
        "priority": "--priority",
    },
}
def run(args) -> int:
    cwd = Path.cwd()
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        agent = require_implementer(args.agent, config, flag="--agent")
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if config.worker is None:
        print(
            "This checkout is not registered as an issuekit worker. "
            "Run `issuekit add` first.",
            file=sys.stderr,
        )
        return 1
    if not config.api_url:
        print(
            "This checkout is registered as an issuekit worker, but api_url is not configured.",
            file=sys.stderr,
        )
        return 1
    if args.interval < 0:
        print("--interval must be non-negative.", file=sys.stderr)
        return 1
    heartbeat_interval = (
        args.heartbeat_interval
        if args.heartbeat_interval is not None
        else config.worker_heartbeat_interval_sec
    )
    if heartbeat_interval <= 0:
        print("--heartbeat-interval must be greater than zero.", file=sys.stderr)
        return 1
    if args.max_heartbeat_failures < 0:
        print("--max-heartbeat-failures must be non-negative.", file=sys.stderr)
        return 1
    if args.max_run_failures < 0:
        print("--max-run-failures must be non-negative.", file=sys.stderr)
        return 1
    warn_if_staleness_not_wider(DEFAULT_STALE_AFTER_SEC, heartbeat_interval)
    if args.max_issues is not None and args.max_issues < 1:
        print("--max-issues must be greater than zero.", file=sys.stderr)
        return 1
    mode = _resolve_mode(args)
    conflict = _mode_option_conflict(args, mode)
    if conflict is not None:
        print(conflict, file=sys.stderr)
        return 1
    if args.proposal_check_limit < 1:
        print("--proposal-check-limit must be greater than zero.", file=sys.stderr)
        return 1

    role = {
        ServeMode.IMPLEMENT: "implementer",
        ServeMode.REVIEW: "reviewer",
        ServeMode.PROPOSAL_CHECKS: "triage",
    }[mode]
    try:
        adapter = preflight_agent(
            agent,
            config=config,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            role=role,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Agent preflight failed: {exc}", file=sys.stderr)
        return 1

    try:
        run_dir = prepare_run_dir(cwd)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    lock_path = run_dir / "serve.lock"
    log_path = run_dir / "serve.log"
    controller = ShutdownController.create()
    controller.on_signal = lambda signum, count: _log(
        sys.stderr, None, "signal", signum=signum, count=count
    )

    try:
        with _serve_lock(lock_path), _signal_handlers(controller):
            with _worker_heartbeat(
                config,
                cwd,
                log_path,
                heartbeat_interval,
                controller,
                max_failures=args.max_heartbeat_failures,
            ):
                return _serve_loop(
                    args,
                    mode=mode,
                    agent=agent,
                    config=config,
                    cwd=cwd,
                    log_path=log_path,
                    controller=controller,
                    adapter=adapter,
                )
    except ServeLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def _retry_pending_holds(
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
            _log(
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
                recreate_store=_should_recreate_store(exc),
            )
        del pending_holds[issue_id]
        _log(
            sys.stderr,
            log_path,
            "held_for_release",
            issue=issue_id,
            origin=origin,
            reason=reason,
        )
    return None


def _serve_loop(
    args,
    *,
    mode: ServeMode,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    adapter: AgentAdapter | None = None,
) -> int:
    if mode is ServeMode.PROPOSAL_CHECKS:
        return _serve_proposal_checks_loop(
            args,
            agent=agent,
            config=config,
            cwd=cwd,
            log_path=log_path,
            controller=controller,
        )

    store = get_store(config) if config.api_url else None
    try:
        if mode is ServeMode.REVIEW:
            review_store = store
            store = None
            return _serve_review_loop(
                args,
                agent=agent,
                config=config,
                cwd=cwd,
                log_path=log_path,
                controller=controller,
                store=review_store,
            )

        backoff = Backoff()
        pending_holds: dict[int, tuple[str, str]] = {}
        attempted_issue_ids: set[int] = set()
        last_failed_issue_id: int | None = None

        def poll(attempt: int, backoff_seconds: float):
            nonlocal last_failed_issue_id
            hold_result = _retry_pending_holds(
                pending_holds,
                config=config,
                log_path=log_path,
                backoff_seconds=backoff_seconds,
            )
            if hold_result is not None:
                return hold_result

            if adapter is not None:
                try:
                    adapter.resolve_binary()
                except (FileNotFoundError, RuntimeError, ValueError) as exc:
                    _log(
                        sys.stderr,
                        log_path,
                        "preflight_error",
                        error=str(exc),
                        backoff=backoff_seconds,
                    )
                    return PollResult(status="error", exit_code=1)

            if _triage_enabled(args, config):
                try:
                    if config.triage.author_agent:
                        decisions = _run_triage_author_cycle(
                            args,
                            config=config,
                            cwd=cwd,
                            log_path=log_path,
                            controller=controller,
                        )
                        hold_failed = False
                        for decision in decisions:
                            if decision.held:
                                _log(
                                    sys.stderr,
                                    log_path,
                                    "held_for_release",
                                    issue=decision.issue_id,
                                    origin=decision.origin,
                                    reason="triage author",
                                )
                            if decision.hold_error and decision.issue_id is not None:
                                pending_holds[decision.issue_id] = (
                                    decision.origin,
                                    "triage author",
                                )
                                hold_failed = True
                                _log(
                                    sys.stderr,
                                    log_path,
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
                            _log(
                                sys.stderr,
                                log_path,
                                "triage_adoption_error",
                                proposal=proposal_id,
                                error=str(error),
                                backoff=backoff_seconds,
                            )

                        hold_failed = False
                        for outcome in auto_adopt_incoming_proposals(
                            config,
                            on_adoption_error=log_adoption_error,
                        ):
                            _log(
                                sys.stderr,
                                log_path,
                                "auto_adopted",
                                proposal=outcome.get("proposal_id"),
                                issue=outcome.get("issue_id"),
                                priority=config.triage.default_priority,
                            )
                            if outcome.get("held"):
                                _log(
                                    sys.stderr,
                                    log_path,
                                    "held_for_release",
                                    issue=outcome.get("issue_id"),
                                    proposal=outcome.get("proposal_id"),
                                    origin=outcome.get("hold_origin")
                                    or outcome.get("origin"),
                                    reason="serve auto-adopt",
                                )
                            if outcome.get("hold_error"):
                                issue_id = outcome.get("issue_id")
                                if issue_id is not None:
                                    origin = str(outcome.get("hold_origin", ""))
                                    reason = str(
                                        outcome.get("hold_reason", "serve auto-adopt")
                                    )
                                    pending_holds[int(issue_id)] = (origin, reason)
                                hold_failed = True
                                _log(
                                    sys.stderr,
                                    log_path,
                                    "hold_error",
                                    issue=outcome.get("issue_id"),
                                    proposal=outcome.get("proposal_id"),
                                    error=outcome.get("hold_error"),
                                    backoff=backoff_seconds,
                                )
                        if hold_failed:
                            return PollResult(status="error", exit_code=1)
                except (ProposalError, TimeoutError, WorkflowError, ValueError) as exc:
                    _log(
                        sys.stderr,
                        log_path,
                        "triage_error",
                        error=str(exc),
                        backoff=backoff_seconds,
                    )
                    return PollResult(
                        status="error",
                        exit_code=1,
                        recreate_store=_should_recreate_store(exc),
                    )
            try:
                implementing_issues = _find_implementing_issues(config, store=store)
            except (RuntimeError, TimeoutError, ValueError) as exc:
                _log(sys.stderr, log_path, "recovery_error", error=str(exc))
                return PollResult(
                    status="error",
                    exit_code=1,
                    recreate_store=_should_recreate_store(exc),
                )

            if implementing_issues:
                issue = implementing_issues[0]
                event = "retrying" if issue.id in attempted_issue_ids else "recovered"
                _log(sys.stderr, log_path, event, issue=issue.id, agent=agent)
            else:
                last_failed_issue_id = None
                try:
                    issue = claim_next(
                        agent,
                        priority=args.priority,
                        config=config,
                        store=store,
                        cwd=cwd,
                        allow_any_branch=getattr(args, "allow_any_branch", False),
                        no_sync=getattr(args, "no_sync", False),
                    )
                except (TimeoutError, WorkflowError, ValueError) as exc:
                    _log(
                        sys.stderr,
                        log_path,
                        "claim_error",
                        error=str(exc),
                        backoff=backoff_seconds,
                    )
                    return PollResult(
                        status="error",
                        exit_code=1,
                        recreate_store=_should_recreate_store(exc),
                    )

            if issue is None:
                return PollResult(status="idle")

            if issue.id is not None:
                attempted_issue_ids.add(issue.id)
            if not implementing_issues:
                _log(sys.stderr, log_path, "claimed", issue=issue.id, agent=agent)
            result = _run_claimed_issue(
                args,
                issue,
                agent=agent,
                config=config,
                cwd=cwd,
                log_path=log_path,
                controller=controller,
                backoff=backoff_seconds,
                adapter=adapter,
                store=store,
            )
            if result.status == "error":
                last_failed_issue_id = issue.id
                return PollResult(
                    "error", 1, result.recreate_store, issue_id=issue.id
                )
            if result.status == "failed":
                last_failed_issue_id = issue.id
                return PollResult(
                    "failed",
                    result.exit_code,
                    result.recreate_store,
                    issue_id=issue.id,
                )
            last_failed_issue_id = None
            return PollResult(
                "success",
                recreate_store=result.recreate_store,
                value=result.reviewed_issue,
            )

        def recreate() -> None:
            nonlocal store
            store = _recreate_store(store, config)

        def on_failure_limit(result: PollResult, failures: int) -> None:
            issue_id = (
                result.issue_id
                if result.issue_id is not None
                else last_failed_issue_id
            )
            fields: dict[str, object] = {}
            if issue_id is not None:
                fields["issue"] = issue_id
            fields["failures"] = failures
            _log(
                sys.stderr,
                log_path,
                "run_failure_limit",
                **fields,
            )
            if issue_id is not None:
                _release_claim_after_run_error(
                    issue_id,
                    config=config,
                    store=store,
                    err=sys.stderr,
                    reason=f"serve stopped after {failures} consecutive failed runs",
                )

        return run_poll_loop(
            controller,
            backoff,
            poll=poll,
            on_idle=lambda attempt: _log(sys.stderr, log_path, "idle", attempt=attempt),
            on_success=lambda poll_result, count: _log_submitted(
                log_path, poll_result.value, count
            ),
            on_stopped=lambda: _log(sys.stderr, log_path, "stopped"),
            once=args.once,
            interval=float(args.interval),
            max_count=args.max_issues,
            max_consecutive_failures=getattr(args, "max_run_failures", 3),
            on_failure_limit=on_failure_limit,
            recreate_store=recreate,
            abort_failed_exit_code=0,
        )
    finally:
        _close_store(store)


def _resolve_mode(args) -> ServeMode:
    if args.review:
        return ServeMode.REVIEW
    if args.proposal_checks:
        return ServeMode.PROPOSAL_CHECKS
    return ServeMode.IMPLEMENT


def _mode_option_conflict(args, mode: ServeMode) -> str | None:
    mode_option = _MODE_OPTIONS.get(mode)
    for attribute, option in _MODE_REJECTED_OPTIONS[mode].items():
        if getattr(args, attribute):
            return f"{mode_option} cannot be combined with {option}."
    return None


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
        hold_result = _retry_pending_holds(
            pending_holds,
            config=config,
            log_path=log_path,
            backoff_seconds=backoff_seconds,
        )
        if hold_result is not None:
            return hold_result
        _log(
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
                log=lambda event, **fields: _log(sys.stderr, log_path, event, **fields),
                err=sys.stderr,
                abort_event=controller.abort_event,
            )
        except AGENT_RUN_ERRORS as exc:
            _log(
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
            _log(
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
        on_idle=lambda attempt: _log(
            sys.stderr, log_path, "proposal_checks_idle", attempt=attempt
        ),
        on_success=lambda _result, _count: None,
        on_stopped=lambda: _log(sys.stderr, log_path, "stopped"),
        once=args.once,
        interval=float(args.interval),
        max_count=None,
        max_consecutive_failures=getattr(args, "max_run_failures", 3),
        on_failure_limit=lambda _result, failures: _log(
            sys.stderr,
            log_path,
            "proposal_check_failure_limit",
            failures=failures,
        ),
        stop_before_retry_sleep=True,
        sleep_after_success=True,
    )


def _serve_review_loop(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    store,
) -> int:
    backoff = Backoff()
    failed_review_ids: set[int] = set()
    try:
        def poll(attempt: int, backoff_seconds: float):
            try:
                issue = next_review(
                    agent,
                    config=config,
                    store=store,
                    include_open=True,
                    exclude_ids=failed_review_ids,
                )
            except (TimeoutError, WorkflowError, ValueError) as exc:
                _log(sys.stderr, log_path, "review_poll_error", error=str(exc), backoff=backoff_seconds)
                return PollResult(
                    "error", 1, recreate_store=_should_recreate_store(exc)
                )

            for issue_id in sorted(failed_review_ids):
                _log(
                    sys.stderr,
                    log_path,
                    "review_skipped",
                    issue=issue_id,
                    reason="previous_failure",
                )

            if issue is None:
                return PollResult("idle")

            _log(sys.stderr, log_path, "reviewing", issue=issue.id, agent=agent)
            result = _run_review_issue(
                args,
                issue,
                agent=agent,
                config=config,
                cwd=cwd,
                log_path=log_path,
                controller=controller,
                backoff=backoff_seconds,
                store=store,
            )
            if result.status == "error":
                if issue.id is not None:
                    failed_review_ids.add(issue.id)
                return PollResult(
                    "error", 1, result.recreate_store, issue_id=issue.id
                )
            if result.status == "failed":
                if issue.id is not None:
                    failed_review_ids.add(issue.id)
                return PollResult(
                    "failed",
                    result.exit_code,
                    result.recreate_store,
                    issue_id=issue.id,
                )
            return PollResult("success", value=result.reviewed_issue)

        def recreate() -> None:
            nonlocal store
            store = _recreate_store(store, config)

        def on_failure_limit(result: PollResult, failures: int) -> None:
            fields: dict[str, object] = {}
            if result.issue_id is not None:
                fields["issue"] = result.issue_id
            fields["failures"] = failures
            _log(
                sys.stderr,
                log_path,
                "review_failure_limit",
                **fields,
            )

        return run_poll_loop(
            controller,
            backoff,
            poll=poll,
            on_idle=lambda attempt: _log(
                sys.stderr, log_path, "review_idle", attempt=attempt
            ),
            on_success=lambda result, count: _log_reviewed(log_path, result.value, count),
            on_stopped=lambda: _log(sys.stderr, log_path, "stopped"),
            once=args.once,
            interval=float(args.interval),
            max_count=args.max_issues,
            max_consecutive_failures=getattr(args, "max_run_failures", 3),
            on_failure_limit=on_failure_limit,
            recreate_store=recreate,
            abort_failed_exit_code=0,
        )
    finally:
        _close_store(store)


def _log_submitted(log_path: Path, reviewed_issue: Issue | None, submitted_count: int) -> None:
    if reviewed_issue is None:
        return
    _log(
        sys.stderr,
        log_path,
        "submitted",
        issue=reviewed_issue.id,
        assignee=reviewed_issue.assignee,
        stage=reviewed_issue.stage,
        count=submitted_count,
    )


def _log_reviewed(log_path: Path, reviewed_issue: Issue | None, decided_count: int) -> None:
    if reviewed_issue is None:
        return
    _log(
        sys.stderr,
        log_path,
        "reviewed",
        issue=reviewed_issue.id,
        assignee=reviewed_issue.assignee,
        stage=reviewed_issue.stage,
        status=reviewed_issue.issue_status,
        count=decided_count,
    )


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
        _log(sys.stderr, log_path, event, **fields)

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


@contextmanager
def _worker_heartbeat(
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    interval: float,
    controller: ShutdownController,
    *,
    max_failures: int = 0,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
) -> Iterator[None]:
    failure_started: datetime | None = None
    escalated = False

    def on_error(
        exc: Exception,
        consecutive_failures: int,
        last_success: datetime | None,
    ) -> None:
        nonlocal escalated, failure_started
        now = datetime.now(UTC)
        if consecutive_failures == 0:
            failure_started = None
            escalated = False
        elif consecutive_failures == 1:
            failure_started = last_success or now
            escalated = False
        last_success_text = (
            last_success.replace(microsecond=0).isoformat()
            if last_success is not None
            else "none"
        )
        _log(
            sys.stderr,
            log_path,
            "worker_registry_error",
            consecutive=consecutive_failures,
            last_success=last_success_text,
            error=str(exc),
        )
        failure_window_sec = (
            (now - failure_started).total_seconds()
            if failure_started is not None
            else 0.0
        )
        if failure_window_sec > stale_after_sec and not escalated:
            escalated = True
            _log(
                sys.stderr,
                log_path,
                "worker_registry_escalated",
                consecutive=consecutive_failures,
                last_success=last_success_text,
                failure_window_sec=int(failure_window_sec),
                stale_after_sec=stale_after_sec,
            )
        if max_failures > 0 and consecutive_failures == max_failures:
            controller.request()

    heartbeat = WorkerHeartbeat(
        config,
        cwd,
        interval=interval,
        on_error=on_error,
    )
    try:
        heartbeat.beat()
    except Exception as exc:
        heartbeat.record_failure(exc)
    heartbeat.start()
    try:
        yield
    finally:
        heartbeat.stop()


@contextmanager
def _signal_handlers(controller: ShutdownController) -> Iterator[None]:
    with installed_signal_handlers(
        {
            signal.SIGINT: controller.handle_signal,
            signal.SIGTERM: controller.handle_signal,
        }
    ):
        yield
