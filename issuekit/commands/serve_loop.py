"""Shared polling primitives for the serve command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
import threading
from types import FrameType
from typing import Callable, Literal

from issuekit.agents.run_claimed import review_feedback_prompt, run_and_submit
from issuekit.agents.review import ReviewParseError, run_review_and_decide
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


BACKOFF_INITIAL_SEC = 1.0
BACKOFF_MAX_SEC = 60.0


@dataclass
class Backoff:
    current: float = BACKOFF_INITIAL_SEC

    def step(self) -> None:
        self.current = min(self.current * 2, BACKOFF_MAX_SEC)

    def reset(self) -> None:
        self.current = BACKOFF_INITIAL_SEC


@dataclass
class ShutdownController:
    """Signal-aware stop flag for a polling loop."""

    event: threading.Event
    abort_event: threading.Event
    signal_count: int = 0
    on_signal: Callable[[int, int], None] | None = None

    @classmethod
    def create(cls) -> "ShutdownController":
        return cls(event=threading.Event(), abort_event=threading.Event())

    @property
    def requested(self) -> bool:
        return self.event.is_set()

    def request(self) -> None:
        self.event.set()

    def sleep(self, seconds: float) -> bool:
        return self.event.wait(timeout=max(0.0, seconds))

    def handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        self.signal_count += 1
        self.request()
        if self.on_signal is not None:
            self.on_signal(signum, self.signal_count)
        if self.signal_count >= 2:
            self.abort_event.set()


@dataclass(frozen=True)
class PollResult:
    """The outcome of one poll and optional worker run."""

    status: Literal["idle", "error", "failed", "success"]
    exit_code: int = 0
    recreate_store: bool = False
    value: object | None = None


def run_poll_loop(
    controller: ShutdownController,
    backoff: Backoff,
    *,
    poll: Callable[[int, float], PollResult],
    on_idle: Callable[[int], None],
    on_success: Callable[[PollResult, int], None],
    on_stopped: Callable[[], None],
    once: bool,
    interval: float,
    max_count: int | None,
    recreate_store: Callable[[], None] | None = None,
    stop_before_retry_sleep: bool = False,
    abort_failed_exit_code: int | None = None,
    sleep_after_success: bool = False,
) -> int:
    """Run poll attempts until stopped, idle-once, or the success limit is reached."""
    count = 0
    attempt = 0
    while not controller.requested:
        attempt += 1
        result = poll(attempt, backoff.current)
        if result.recreate_store and recreate_store is not None:
            recreate_store()

        if result.status == "idle":
            on_idle(attempt)
            if once:
                return 0
            controller.sleep(interval)
            continue

        if result.status in {"error", "failed"}:
            if once:
                return result.exit_code
            if result.status == "failed" and abort_failed_exit_code is not None:
                if controller.abort_event.is_set():
                    return abort_failed_exit_code
            if stop_before_retry_sleep and controller.requested:
                break
            controller.sleep(backoff.current)
            backoff.step()
            continue

        count += 1
        backoff.reset()
        on_success(result, count)
        if once or (max_count is not None and count >= max_count):
            return 0
        if sleep_after_success:
            if controller.requested:
                break
            controller.sleep(interval)
            if controller.requested:
                break

    on_stopped()
    return 0


def should_recreate_store(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, WorkflowError) and exc.code == "request_failed"


def recreate_store(store, config: IssuekitConfig):
    close_store(store)
    return get_store(config) if config.api_url else None


def close_store(store) -> None:
    if store is not None:
        store.close()


@dataclass(frozen=True)
class IssueRunResult:
    status: str
    exit_code: int
    reviewed_issue: Issue | None = None
    recreate_store: bool = False


def run_claimed_issue(
    args,
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    issues_dir: Path,
    log_path: Path,
    controller: ShutdownController,
    backoff: float,
    store=None,
) -> IssueRunResult:
    try:
        outcome = run_and_submit(
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            issues_dir=issues_dir,
            timeout=float(args.timeout_sec),
            model=getattr(args, "model", None),
            reasoning_effort=getattr(args, "reasoning_effort", None),
            prompt_suffix=review_feedback_prompt(issue.body),
            abort_event=controller.abort_event,
            store=store,
            out=sys.stderr,
            err=sys.stderr,
            allow_any_branch=getattr(args, "allow_any_branch", False),
        )
    except (FileNotFoundError, RuntimeError, ValueError, TimeoutError, WorkflowError) as exc:
        log_event(
            sys.stderr, log_path, "run_error", issue=issue.id, error=str(exc), backoff=backoff
        )
        return IssueRunResult("error", 1, recreate_store=should_recreate_store(exc))

    if outcome.exit_code != 0 or outcome.reviewed_issue is None:
        log_event(
            sys.stderr,
            log_path,
            "run_failed",
            issue=issue.id,
            exit_code=outcome.exit_code,
            backoff=backoff,
        )
        return IssueRunResult("failed", outcome.exit_code)

    return IssueRunResult("submitted", 0, reviewed_issue=outcome.reviewed_issue)


def run_review_issue(
    args,
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    backoff: float,
    store=None,
) -> IssueRunResult:
    try:
        outcome = run_review_and_decide(
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            timeout=float(args.timeout_sec),
            model=getattr(args, "model", None),
            reasoning_effort=getattr(args, "reasoning_effort", None),
            abort_event=controller.abort_event,
            store=store,
            out=sys.stderr,
            err=sys.stderr,
        )
    except (
        FileNotFoundError,
        RuntimeError,
        ValueError,
        TimeoutError,
        WorkflowError,
        ReviewParseError,
    ) as exc:
        log_event(
            sys.stderr,
            log_path,
            "review_error",
            issue=issue.id,
            error=str(exc),
            backoff=backoff,
        )
        return IssueRunResult("error", 1, recreate_store=should_recreate_store(exc))

    if outcome.exit_code != 0 or outcome.decided_issue is None:
        log_event(
            sys.stderr,
            log_path,
            "review_failed",
            issue=issue.id,
            exit_code=outcome.exit_code,
            backoff=backoff,
        )
        return IssueRunResult("failed", outcome.exit_code)

    return IssueRunResult("reviewed", 0, reviewed_issue=outcome.decided_issue)


def recover_orphaned_issues(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    issues_dir: Path,
    log_path: Path,
    controller: ShutdownController,
    submitted_count: int,
    backoff: Backoff,
    store,
    log_submitted: Callable[[Path, Issue | None, int], None],
) -> tuple[int, int | None, object]:
    if config.worker is None:
        return submitted_count, None, store

    me = config.worker_key()
    if me is None:
        return submitted_count, None, store
    try:
        issues = []
        seen: set[int] = set()
        for worker_key in config.worker_lookup_keys():
            for issue in store.find_implementing_for_worker(worker_key):
                if issue.id not in seen:
                    seen.add(issue.id)
                    issues.append(issue)
    except (AttributeError, RuntimeError, TimeoutError, WorkflowError, ValueError) as exc:
        log_event(sys.stderr, log_path, "recovery_error", worker=me, error=str(exc))
        if should_recreate_store(exc):
            store = recreate_store(store, config)
        return submitted_count, None, store

    for issue in issues:
        if controller.requested:
            break
        log_event(sys.stderr, log_path, "recovered", issue=issue.id, agent=agent, worker=me)
        result = run_claimed_issue(
            args,
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            issues_dir=issues_dir,
            log_path=log_path,
            controller=controller,
            backoff=backoff.current,
        )
        if result.status == "submitted":
            submitted_count += 1
            backoff.reset()
            log_submitted(log_path, result.reviewed_issue, submitted_count)
            if args.once or (
                args.max_issues is not None and submitted_count >= args.max_issues
            ):
                return submitted_count, 0, store
            continue

        if result.status == "failed" and controller.abort_event.is_set():
            return submitted_count, 0, store
        if result.recreate_store:
            store = recreate_store(store, config)
        if not args.once:
            controller.sleep(backoff.current)
            backoff.step()

    return submitted_count, None, store


def log_event(stream, log_path: Path | None, event: str, **fields: object) -> None:
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    parts = [f"ts={timestamp}", f"event={event}"]
    parts.extend(f"{key}={value}" for key, value in fields.items())
    line = " ".join(parts)
    print(line, file=stream)
    if log_path is not None:
        with log_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
