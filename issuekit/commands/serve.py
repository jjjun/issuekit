"""Implementation of the serve command."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Iterator

from issuekit.agents.proposal_check import (
    ProposalCheckParseError,
    run_proposal_check_cycle,
)
from issuekit.agentrun import AgentRunner
from issuekit.agents.triage_author import run_triage_author_cycle
from issuekit.config import IssuekitConfig, load_config
from issuekit.commands.serve_loop import (
    Backoff,
    BACKOFF_INITIAL_SEC,
    BACKOFF_MAX_SEC,
    PollResult,
    ShutdownController,
    close_store as _close_store,
    log_event as _log,
    recreate_store as _recreate_store,
    recover_orphaned_issues as _recover_orphaned_issues,
    run_claimed_issue as _run_claimed_issue,
    run_poll_loop,
    run_review_issue as _run_review_issue,
    should_recreate_store as _should_recreate_store,
)
from issuekit.core import Issue
from issuekit.proposals import ProposalError
from issuekit.proposals.api import auto_adopt_incoming_proposals
from issuekit.store import get_store
from issuekit.workers.registry import (
    WORKER_HEARTBEAT_INTERVAL_SEC,
    WorkerHeartbeat,
    try_post_worker_registration,
)
from issuekit.workflow import WorkflowError, claim_next, next_review, resolve_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    serve_parser = subparsers.add_parser(
        "serve",
        help="Poll for eligible issues and run this checkout's worker agent.",
    )
    serve_parser.add_argument("--agent", help="Configured agent name to run.")
    serve_parser.add_argument(
        "--model",
        help=(
            "Optional model name applied to every agent launched by this serve loop; "
            "use per-agent config for mixed-agent model selection."
        ),
    )
    serve_parser.add_argument(
        "--reasoning-effort",
        help=(
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
    serve_parser.add_argument(
        "--review",
        action="store_true",
        help="Poll the review pool and run this checkout's reviewer agent.",
    )
    serve_parser.add_argument(
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
    serve_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=1800.0,
        help="Hard timeout for each agent run in seconds.",
    )
    serve_parser.add_argument(
        "--allow-any-branch",
        action="store_true",
        help="Override the configured work_branch guard for human recovery.",
    )
    serve_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip the claim-time clean checkout and fast-forward sync guard.",
    )
    serve_parser.set_defaults(func=run)


class ServeLockError(RuntimeError):
    """Raised when another live serve process holds the checkout lock."""


def run(args) -> int:
    cwd = Path.cwd()
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    agent = resolve_implementer(args.agent, config)
    if agent is None:
        print(
            "No implementer is configured. Pass --agent, set default_implementer, "
            "or configure exactly one enabled assignee.",
            file=sys.stderr,
        )
        return 1
    if config.worker is None:
        print(
            "This checkout is not registered as an issuekit worker. "
            "Run `issuekit add` first.",
            file=sys.stderr,
        )
        return 1
    if args.interval < 0:
        print("--interval must be non-negative.", file=sys.stderr)
        return 1
    if args.max_issues is not None and args.max_issues < 1:
        print("--max-issues must be greater than zero.", file=sys.stderr)
        return 1
    if args.review and args.triage:
        print("--review cannot be combined with --triage.", file=sys.stderr)
        return 1
    if args.proposal_checks and args.triage:
        print("--proposal-checks cannot be combined with --triage.", file=sys.stderr)
        return 1
    if args.proposal_checks and args.review:
        print("--proposal-checks cannot be combined with --review.", file=sys.stderr)
        return 1
    if args.proposal_checks and args.priority is not None:
        print("--proposal-checks cannot be combined with --priority.", file=sys.stderr)
        return 1
    if args.proposal_check_limit < 1:
        print("--proposal-check-limit must be greater than zero.", file=sys.stderr)
        return 1

    issues_dir = config.issues_path(cwd)
    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    lock_path = run_dir / "serve.lock"
    log_path = run_dir / "serve.log"
    controller = ShutdownController.create()
    controller.on_signal = lambda signum, count: _log(
        sys.stderr, None, "signal", signum=signum, count=count
    )

    try:
        with _serve_lock(lock_path), _signal_handlers(controller):
            with _worker_heartbeat(config, cwd, log_path):
                return _serve_loop(
                    args,
                    agent=agent,
                    config=config,
                    cwd=cwd,
                    issues_dir=issues_dir,
                    log_path=log_path,
                    controller=controller,
                )
    except ServeLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def _serve_loop(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    issues_dir: Path,
    log_path: Path,
    controller: ShutdownController,
) -> int:
    submitted_count = 0
    if getattr(args, "proposal_checks", False):
        return _serve_proposal_checks_loop(
            args,
            agent=agent,
            config=config,
            cwd=cwd,
            log_path=log_path,
            controller=controller,
        )

    store = get_store(config) if config.api_url else None
    recovery_store = None
    try:
        if getattr(args, "review", False):
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
        recovery_store = get_store(config) if config.api_url else None
        submitted_count, exit_code, recovery_store = _recover_orphaned_issues(
            args,
            agent=agent,
            config=config,
            cwd=cwd,
            issues_dir=issues_dir,
            log_path=log_path,
            controller=controller,
            submitted_count=submitted_count,
            backoff=backoff,
            store=recovery_store,
            log_submitted=_log_submitted,
        )
        if exit_code is not None:
            return exit_code

        def poll(attempt: int, backoff_seconds: float):
            if _triage_enabled(args, config):
                try:
                    if config.triage.author_agent:
                        _run_triage_author_cycle(
                            args,
                            config=config,
                            cwd=cwd,
                            log_path=log_path,
                            controller=controller,
                        )
                    else:
                        for outcome in auto_adopt_incoming_proposals(config):
                            _log(
                                sys.stderr,
                                log_path,
                                "auto_adopted",
                                proposal=outcome.get("proposal_id"),
                                issue=outcome.get("issue_id"),
                                priority=config.triage.default_priority,
                            )
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
                _log(sys.stderr, log_path, "claim_error", error=str(exc), backoff=backoff_seconds)
                return PollResult(
                    status="error",
                    exit_code=1,
                    recreate_store=_should_recreate_store(exc),
                )

            if issue is None:
                return PollResult(status="idle")

            _log(sys.stderr, log_path, "claimed", issue=issue.id, agent=agent)
            result = _run_claimed_issue(
                args,
                issue,
                agent=agent,
                config=config,
                cwd=cwd,
                issues_dir=issues_dir,
                log_path=log_path,
                controller=controller,
                backoff=backoff_seconds,
                store=store,
            )
            if result.status == "error":
                return PollResult("error", 1, result.recreate_store)
            if result.status == "failed":
                return PollResult("failed", result.exit_code, result.recreate_store)
            return PollResult(
                "success",
                recreate_store=result.recreate_store,
                value=result.reviewed_issue,
            )

        def recreate() -> None:
            nonlocal store
            store = _recreate_store(store, config)

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
            max_count=args.max_issues - submitted_count if args.max_issues is not None else None,
            recreate_store=recreate,
            abort_failed_exit_code=0,
        )
    finally:
        _close_store(store)
        _close_store(recovery_store)


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

    def poll(attempt: int, backoff_seconds: float):
        nonlocal answered_count
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
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            ProposalError,
            ProposalCheckParseError,
        ) as exc:
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
    try:
        def poll(attempt: int, backoff_seconds: float):
            try:
                issue = next_review(agent, config=config, store=store, include_open=True)
            except (TimeoutError, WorkflowError, ValueError) as exc:
                _log(sys.stderr, log_path, "review_poll_error", error=str(exc), backoff=backoff_seconds)
                return PollResult(
                    "error", 1, recreate_store=_should_recreate_store(exc)
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
                return PollResult("error", 1, result.recreate_store)
            if result.status == "failed":
                return PollResult("failed", result.exit_code, result.recreate_store)
            return PollResult("success", value=result.reviewed_issue)

        def recreate() -> None:
            nonlocal store
            store = _recreate_store(store, config)

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
) -> None:
    def emit(event: str, **fields: object) -> None:
        _log(sys.stderr, log_path, event, **fields)

    run_triage_author_cycle(
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
def _worker_heartbeat(config: IssuekitConfig, cwd: Path, log_path: Path) -> Iterator[None]:
    def on_error(exc: Exception) -> None:
        _log(sys.stderr, log_path, "worker_registry_error", error=str(exc))

    try_post_worker_registration(config, cwd, on_error=on_error)
    heartbeat = WorkerHeartbeat(
        config,
        cwd,
        interval=WORKER_HEARTBEAT_INTERVAL_SEC,
        on_error=on_error,
    )
    heartbeat.start()
    try:
        yield
    finally:
        heartbeat.stop()


@contextmanager
def _serve_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(exist_ok=True)
    pid = os.getpid()
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing_pid = _read_lock_pid(lock_path)
            if existing_pid is not None and _pid_is_live(existing_pid):
                raise ServeLockError(
                    f"issuekit serve is already running for this checkout (pid {existing_pid})."
                )
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(f"{pid}\n")
            break

    try:
        yield
    finally:
        try:
            if _read_lock_pid(lock_path) == pid:
                lock_path.unlink()
        except FileNotFoundError:
            pass


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and f'"{pid}"' in result.stdout


@contextmanager
def _signal_handlers(controller: ShutdownController) -> Iterator[None]:
    previous: dict[int, signal.Handlers] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, controller.handle_signal)
        except (ValueError, OSError, AttributeError):
            pass
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError, AttributeError):
                pass
