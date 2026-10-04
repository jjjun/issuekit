"""Run status watcher and heartbeat output."""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from issuekit.agentrun.logtail import last_nonempty_line_of
from issuekit.agentrun.status import (
    HEARTBEAT_INTERVAL_SEC,
    RunStatus,
    write_status,
)
from issuekit.gitutil import changed_file_count

HEARTBEAT_GIT_INTERVAL_SEC = 5.0


class _RunWatcher:
    """Background watcher that updates status JSON and optionally emits a heartbeat."""

    def __init__(
        self,
        *,
        run_status_path: Path,
        run_status: RunStatus,
        repo: Path,
        agent_log_path: Path,
        stdout_log_path: Path | None = None,
        enable_heartbeat: bool,
        start_time: float,
    ) -> None:
        self.run_status_path = run_status_path
        self.run_status = run_status
        self.repo = repo
        self.agent_log_path = agent_log_path
        self.stdout_log_path = stdout_log_path
        self.enable_heartbeat = enable_heartbeat
        self.start_time = start_time
        self._last_git_status_at: float | None = None
        self._changed_file_count = 0
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 - a tick must never kill the writer
                sys.stderr.write(f"\nstatus writer tick failed (continuing): {exc}\n")
                sys.stderr.flush()
            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL_SEC)

    def _tick(self) -> None:
        last_line = self._read_latest_log_line()
        now = datetime.now().replace(microsecond=0).isoformat()

        self.run_status = replace(
            self.run_status,
            last_log_line=last_line,
            last_log_at=now if last_line else self.run_status.last_log_at,
            heartbeat_at=now,
        )
        write_status(self.run_status_path, self.run_status)

        if self.enable_heartbeat:
            changed = self._heartbeat_changed_file_count()
            elapsed = time.monotonic() - self.start_time
            minutes, seconds = divmod(int(elapsed), 60)
            line_text = last_line or "-"
            if len(line_text) > 50:
                line_text = line_text[:47] + "..."
            msg = (
                f"[{minutes:02d}:{seconds:02d}] running run={self.run_status.run_id} "
                f"changed={changed} last: {line_text}"
            )
            max_width = 100
            if len(msg) > max_width:
                msg = msg[: max_width - 3] + "..."
            sys.stderr.write(f"\r{msg}")
            sys.stderr.flush()

    def _heartbeat_changed_file_count(self) -> int:
        now = time.monotonic()
        if (
            self._last_git_status_at is None
            or now - self._last_git_status_at >= HEARTBEAT_GIT_INTERVAL_SEC
        ):
            self._last_git_status_at = now
            self._changed_file_count = changed_file_count(self.repo)
        return self._changed_file_count

    def _read_latest_log_line(self) -> str | None:
        stderr_entry = _read_log_entry(self.agent_log_path)
        if self.stdout_log_path is None:
            return stderr_entry[0] if stderr_entry is not None else None

        stdout_entry = _read_log_entry(self.stdout_log_path)
        if stdout_entry is None:
            return stderr_entry[0] if stderr_entry is not None else None

        summary = _summarize_json_event(stdout_entry[0])
        if summary is None:
            return stderr_entry[0] if stderr_entry is not None else None
        if stderr_entry is None or stdout_entry[1] >= stderr_entry[1]:
            return summary
        return stderr_entry[0]


def _read_log_entry(path: Path) -> tuple[str, int] | None:
    return last_nonempty_line_of(path)


def _summarize_json_event(line: str) -> str | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        return None

    event_type = event["type"]
    summary = event_type
    if event_type == "item.completed":
        item = event.get("item")
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            item_type = item["type"]
            summary = f"{event_type} {item_type}"
            command = item.get("command")
            if item_type == "command_execution" and isinstance(command, str):
                summary += f": {' '.join(command.split())}"

    summary = summary.encode("ascii", errors="replace").decode("ascii")
    if len(summary) > 200:
        summary = summary[:197] + "..."
    return summary
