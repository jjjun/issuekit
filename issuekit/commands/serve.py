"""Implementation of the serve command."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import FrameType
from typing import Iterator

from issuekit.agents.run_claimed import review_feedback_prompt, run_and_submit
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue
from issuekit.store import get_store
from issuekit.worker_registry import (
    WORKER_HEARTBEAT_INTERVAL_SEC,
    WorkerHeartbeat,
    try_post_worker_registration,
)
from issuekit.workflow import WorkflowError, claim_next


BACKOFF_INITIAL_SEC = 1.0
BACKOFF_MAX_SEC = 60.0


class ServeLockError(RuntimeError):
    """Raised when another live serve process holds the checkout lock."""


@dataclass(frozen=True)
class IssueRunResult:
    status: str
    exit_code: int
    reviewed_issue: Issue | None = None
    recreate_store: bool = False


@dataclass
class ShutdownController:
    """Signal-aware stop flag for the serve loop."""

    event: threading.Event
    abort_event: threading.Event
    signal_count: int = 0

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
        _log(sys.stderr, None, "signal", signum=signum, count=self.signal_count)
        if self.signal_count >= 2:
            self.abort_event.set()


def run(args) -> int:
    cwd = Path.cwd()
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    agent = _resolve_agent(args.agent, config)
    if agent is None:
        print(
            "--agent is required unless exactly one assignee is configured.",
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

    issues_dir = config.issues_path(cwd)
    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    lock_path = run_dir / "serve.lock"
    log_path = run_dir / "serve.log"
    controller = ShutdownController.create()

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
    backoff = BACKOFF_INITIAL_SEC
    attempt_count = 0
    store = get_store(config) if config.api_url else None
    recovery_store = get_store(config) if config.api_url else None
    try:
        submitted_count, backoff, exit_code, recovery_store = _recover_orphaned_issues(
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
        )
        if exit_code is not None:
            return exit_code

        while not controller.requested:
            attempt_count += 1
            try:
                issue = claim_next(
                    agent,
                    priority=args.priority,
                    config=config,
                    store=store,
                )
            except (TimeoutError, WorkflowError, ValueError) as exc:
                _log(sys.stderr, log_path, "claim_error", error=str(exc), backoff=backoff)
                if _should_recreate_store(exc):
                    store = _recreate_store(store, config)
                if args.once:
                    return 1
                controller.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue

            if issue is None:
                _log(sys.stderr, log_path, "idle", attempt=attempt_count)
                if args.once:
                    return 0
                controller.sleep(float(args.interval))
                continue

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
                backoff=backoff,
                store=store,
            )
            if result.recreate_store:
                store = _recreate_store(store, config)
            if result.status == "error":
                if args.once:
                    return 1
                controller.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue

            if result.status == "failed":
                if args.once:
                    return result.exit_code
                if controller.abort_event.is_set():
                    return 0
                controller.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue

            submitted_count += 1
            backoff = BACKOFF_INITIAL_SEC
            _log_submitted(log_path, result.reviewed_issue, submitted_count)
            if args.once:
                return 0
            if args.max_issues is not None and submitted_count >= args.max_issues:
                return 0

        _log(sys.stderr, log_path, "stopped")
        return 0
    finally:
        _close_store(store)
        _close_store(recovery_store)


def _recover_orphaned_issues(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    issues_dir: Path,
    log_path: Path,
    controller: ShutdownController,
    submitted_count: int,
    backoff: float,
    store,
) -> tuple[int, float, int | None, object]:
    if config.worker is None:
        return submitted_count, backoff, None, store

    me = config.worker_key()
    if me is None:
        return submitted_count, backoff, None, store
    try:
        issues = store.find_implementing_for_worker(me)
    except (AttributeError, RuntimeError, TimeoutError, WorkflowError, ValueError) as exc:
        _log(sys.stderr, log_path, "recovery_error", worker=me, error=str(exc))
        if _should_recreate_store(exc):
            store = _recreate_store(store, config)
        return submitted_count, backoff, None, store

    for issue in issues:
        if controller.requested:
            break
        _log(sys.stderr, log_path, "recovered", issue=issue.id, agent=agent, worker=me)
        result = _run_claimed_issue(
            args,
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            issues_dir=issues_dir,
            log_path=log_path,
            controller=controller,
            backoff=backoff,
        )
        if result.status == "submitted":
            submitted_count += 1
            backoff = BACKOFF_INITIAL_SEC
            _log_submitted(log_path, result.reviewed_issue, submitted_count)
            if args.once:
                return submitted_count, backoff, 0, store
            if args.max_issues is not None and submitted_count >= args.max_issues:
                return submitted_count, backoff, 0, store
            continue

        if result.status == "failed" and controller.abort_event.is_set():
            return submitted_count, backoff, 0, store
        if result.recreate_store:
            store = _recreate_store(store, config)
        if not args.once:
            controller.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_SEC)

    return submitted_count, backoff, None, store


def _run_claimed_issue(
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
            prompt_suffix=review_feedback_prompt(issue.body),
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
    ) as exc:
        _log(sys.stderr, log_path, "run_error", issue=issue.id, error=str(exc), backoff=backoff)
        return IssueRunResult(
            status="error",
            exit_code=1,
            recreate_store=_should_recreate_store(exc),
        )

    if outcome.exit_code != 0 or outcome.reviewed_issue is None:
        _log(
            sys.stderr,
            log_path,
            "run_failed",
            issue=issue.id,
            exit_code=outcome.exit_code,
            backoff=backoff,
        )
        return IssueRunResult(status="failed", exit_code=outcome.exit_code)

    return IssueRunResult(
        status="submitted",
        exit_code=0,
        reviewed_issue=outcome.reviewed_issue,
    )


def _should_recreate_store(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, WorkflowError) and exc.code == "request_failed"


def _recreate_store(store, config: IssuekitConfig):
    _close_store(store)
    return get_store(config) if config.api_url else None


def _close_store(store) -> None:
    if store is None:
        return
    close = getattr(store, "close", None)
    if close is not None:
        close()


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


def _resolve_agent(agent: str | None, config: IssuekitConfig) -> str | None:
    if agent:
        return agent
    if len(config.assignees) == 1:
        return config.assignees[0]
    return None


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


def _log(stream, log_path: Path | None, event: str, **fields: object) -> None:
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    parts = [f"ts={timestamp}", f"event={event}"]
    parts.extend(f"{key}={value}" for key, value in fields.items())
    line = " ".join(parts)
    print(line, file=stream)
    if log_path is not None:
        with log_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
