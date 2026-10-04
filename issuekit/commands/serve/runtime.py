"""Local process runtime helpers for the serve command."""

from __future__ import annotations

import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from issuekit.agentrun.run_dir import ServeLockError, prepare_run_dir, serve_lock
from issuekit.config import IssuekitConfig
from issuekit.issues.orphans import DEFAULT_STALE_AFTER_SEC
from issuekit.signals import installed_signal_handlers
from issuekit.workers.registration import WorkerHeartbeat

from .loop import ShutdownController, log_event

__all__ = ["ServeLockError", "prepare_run_dir", "serve_lock"]


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
        log_event(
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
            log_event(
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
