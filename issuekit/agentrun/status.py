"""Agent run status records and disk IO."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from issuekit.agentrun._coerce import optional_float, optional_int, optional_str


RunStatusValue = Literal["running", "completed", "failed", "timed_out"]

# Cadence of the background status writer loop (seconds).
HEARTBEAT_INTERVAL_SEC = 1.0
# A running record whose heartbeat is older than this is considered stale.
STALE_AFTER_SEC = 60.0
# Atomic-replace retry budget for write_status (Windows tolerates rename poorly).
_REPLACE_MAX_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05


@dataclass(frozen=True)
class RunStatus:
    """Single on-disk agent run status record."""

    run_id: str
    agent: str
    issue: int | None
    status: RunStatusValue
    pid: int | None
    started_at: str
    ended_at: str | None
    elapsed_sec: float | None
    exit_code: int | None
    plan: str
    stdout_log: str
    agent_log: str
    last_log_line: str | None = None
    last_log_at: str | None = None
    heartbeat_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunStatus":
        return cls(
            run_id=str(data["run_id"]),
            agent=str(data["agent"]),
            issue=optional_int(data.get("issue")),
            status=_status_value(data["status"]),
            pid=optional_int(data.get("pid")),
            started_at=str(data["started_at"]),
            ended_at=optional_str(data.get("ended_at")),
            elapsed_sec=optional_float(data.get("elapsed_sec")),
            exit_code=optional_int(data.get("exit_code")),
            plan=str(data["plan"]),
            stdout_log=str(data["stdout_log"]),
            agent_log=str(data["agent_log"]),
            last_log_line=optional_str(data.get("last_log_line")),
            last_log_at=optional_str(data.get("last_log_at")),
            heartbeat_at=optional_str(data.get("heartbeat_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_active(self) -> bool:
        return self.status == "running"


def status_path(run_dir: Path, run_id: str) -> Path:
    return run_dir / f"{run_id}.status.json"


def write_status(path: Path, status: RunStatus) -> None:
    """Write a status record as UTF-8 JSON, resiliently.

    Prefers an atomic temp-file rename. On Windows ``os.replace`` can raise
    ``PermissionError`` (WinError 5) or a sharing violation (WinError 32) when the
    destination is momentarily open by another handle (a concurrent reader, an
    editor, or antivirus scanning the temp file). We retry the rename with a short
    backoff and, if it still fails, fall back to a best-effort in-place write so the
    status is at least eventually consistent. This never raises on lock contention;
    a failed status write must not be allowed to kill the heartbeat writer.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    content = json.dumps(status.to_dict(), indent=2) + "\n"
    temp_path.write_text(content, encoding="utf-8", newline="\n")

    for attempt in range(_REPLACE_MAX_ATTEMPTS):
        try:
            temp_path.replace(path)
            return
        except OSError:
            if attempt == _REPLACE_MAX_ATTEMPTS - 1:
                break
            time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))

    # Atomic swap kept failing: write the destination directly and drop the temp.
    # The destination may still be held by another handle, so guard this write too:
    # write_status must never raise on IO contention. If even this fails, the record
    # stays as last written on disk and is_stale surfaces it after STALE_AFTER_SEC.
    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError:
        pass
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def read_status(path: Path) -> RunStatus:
    return RunStatus.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_statuses(run_dir: Path) -> list[RunStatus]:
    statuses = [read_status(path) for path in run_dir.glob("*.status.json")]
    return sorted(
        statuses,
        key=lambda status: (status.started_at, status.run_id),
        reverse=True,
    )


def find_status(run_dir: Path, run_id: str) -> RunStatus | None:
    path = status_path(run_dir, run_id)
    if not path.exists():
        return None
    return read_status(path)


def is_stale(status: RunStatus, *, now: datetime | None = None) -> bool:
    """Return True when a running record's heartbeat has gone stale.

    Stale means the run is recorded as ``running`` but its most recent heartbeat
    (falling back to ``started_at``) is older than ``STALE_AFTER_SEC``. Detection is
    purely heartbeat-age based -- no process/pid inspection -- so it stays
    cross-platform and dependency-free. Missing or unparseable timestamps are treated
    as not stale.
    """
    if status.status != "running":
        return False
    stamp = status.heartbeat_at or status.started_at
    if not stamp:
        return False
    try:
        last = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    reference = now or datetime.now()
    try:
        elapsed = (reference - last).total_seconds()
    except TypeError:
        # Mismatched naive/aware datetimes; treat as not stale rather than crash runs.
        return False
    return elapsed > STALE_AFTER_SEC


def repo_relative(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _status_value(value: Any) -> RunStatusValue:
    if value not in {"running", "completed", "failed", "timed_out"}:
        raise ValueError(f"Invalid run status: {value}")
    return value
