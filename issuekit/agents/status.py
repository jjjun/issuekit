"""Agent run status records and disk IO."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


RunStatusValue = Literal["running", "completed", "failed", "timed_out"]


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
    stderr_log: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunStatus":
        return cls(
            run_id=str(data["run_id"]),
            agent=str(data["agent"]),
            issue=_optional_int(data.get("issue")),
            status=_status_value(data["status"]),
            pid=_optional_int(data.get("pid")),
            started_at=str(data["started_at"]),
            ended_at=_optional_str(data.get("ended_at")),
            elapsed_sec=_optional_float(data.get("elapsed_sec")),
            exit_code=_optional_int(data.get("exit_code")),
            plan=str(data["plan"]),
            stdout_log=str(data["stdout_log"]),
            stderr_log=str(data["stderr_log"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_active(self) -> bool:
        return self.status == "running"


def status_path(run_dir: Path, run_id: str) -> Path:
    return run_dir / f"{run_id}.status.json"


def write_status(path: Path, status: RunStatus) -> None:
    """Atomically write a status record as UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    content = json.dumps(status.to_dict(), indent=2) + "\n"
    temp_path.write_text(content, encoding="utf-8", newline="\n")
    temp_path.replace(path)


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


def repo_relative(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _status_value(value: Any) -> RunStatusValue:
    if value not in {"running", "completed", "failed", "timed_out"}:
        raise ValueError(f"Invalid run status: {value}")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
