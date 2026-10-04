"""Shared polling primitives for the serve command."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Literal

from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agents.review import (
    ReviewParseError,
    ReviewRunParseError,
    run_review_and_decide,
)
from issuekit.agents.run_claimed import (
    review_feedback_prompt,
    run_and_submit,
)
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.file_permissions import open_owner_only_new
from issuekit.store import get_store

BACKOFF_INITIAL_SEC = 1.0
BACKOFF_MAX_SEC = 60.0


@dataclass
class Backoff:
    current: float = field(default_factory=lambda: BACKOFF_INITIAL_SEC)

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
    def create(cls) -> ShutdownController:
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
    issue_id: int | None = None


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
    max_consecutive_failures: int = 0,
    on_failure_limit: Callable[[PollResult, int], None] | None = None,
    recreate_store: Callable[[], None] | None = None,
    stop_before_retry_sleep: bool = False,
    abort_failed_exit_code: int | None = None,
    sleep_after_success: bool = False,
) -> int:
    """Run poll attempts until stopped, idle-once, or the success limit is reached."""
    count = 0
    attempt = 0
    consecutive_failures = 0
    while not controller.requested:
        attempt += 1
        result = poll(attempt, backoff.current)
        if result.recreate_store and recreate_store is not None:
            recreate_store()

        if result.status == "idle":
            consecutive_failures = 0
            on_idle(attempt)
            if once:
                return 0
            controller.sleep(interval)
            continue

        if result.status in {"error", "failed"}:
            consecutive_failures += 1
            if (
                max_consecutive_failures > 0
                and consecutive_failures >= max_consecutive_failures
            ):
                if on_failure_limit is not None:
                    on_failure_limit(result, consecutive_failures)
                return 1
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
        consecutive_failures = 0
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
    log_path: Path,
    controller: ShutdownController,
    backoff: float,
    adapter: AgentAdapter | None = None,
    store=None,
) -> IssueRunResult:
    try:
        outcome = run_and_submit(
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            timeout=float(args.timeout_sec),
            model=getattr(args, "model", None),
            reasoning_effort=getattr(args, "reasoning_effort", None),
            adapter=adapter,
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
    except ReviewRunParseError as exc:
        log_event(
            sys.stderr,
            log_path,
            "review_decision_discarded",
            issue=issue.id,
            error=str(exc),
            remedy="rerun_review",
            backoff=backoff,
        )
        return IssueRunResult("error", 1)
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


def find_implementing_issues(config: IssuekitConfig, *, store) -> list[Issue]:
    """Find this checkout's active claims for startup recovery and poll retries."""

    if config.worker is None or config.worker_key() is None or store is None:
        return []
    return store.find_implementing_for_workers(config.worker_lookup_keys())


def log_event(stream, log_path: Path | None, event: str, **fields: object) -> None:
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    parts = [f"ts={timestamp}", f"event={event}"]
    for key, value in fields.items():
        text = str(value)
        if any(character.isspace() for character in text) or "=" in text:
            text = json.dumps(text)
        parts.append(f"{key}={text}")
    line = " ".join(parts)
    print(line, file=stream)
    if log_path is not None:
        with os.fdopen(
            open_owner_only_new(log_path, append=True),
            "a",
            encoding="utf-8",
            newline="\n",
        ) as fh:
            fh.write(line + "\n")
