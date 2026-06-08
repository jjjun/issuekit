"""Implementation of the runs command."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys

from issuekit.agents.status import RunStatus, find_status, list_statuses


TAIL_LINES = 40


def run(args) -> int:
    run_dir = Path.cwd() / ".agent-runs"
    if args.run_id:
        return _print_detail(run_dir, args.run_id, json_output=args.json)
    return _print_list(run_dir, active_only=args.active, json_output=args.json)


def _print_list(run_dir: Path, *, active_only: bool, json_output: bool) -> int:
    statuses = list_statuses(run_dir) if run_dir.exists() else []
    if active_only:
        statuses = [status for status in statuses if status.is_active]

    if json_output:
        print(json.dumps([status.to_dict() for status in statuses], indent=2))
        return 0

    if not statuses:
        print("No runs.")
        return 0

    rows = [
        (
            status.run_id,
            status.agent,
            str(status.issue) if status.issue is not None else "-",
            status.status,
            _format_elapsed(status),
            _format_last_log(status),
        )
        for status in statuses
    ]
    widths = [
        max(len("RUN ID"), *(len(row[0]) for row in rows)),
        max(len("AGENT"), *(len(row[1]) for row in rows)),
        max(len("ISSUE"), *(len(row[2]) for row in rows)),
        max(len("STATUS"), *(len(row[3]) for row in rows)),
        max(len("ELAPSED"), *(len(row[4]) for row in rows)),
        max(len("LAST LOG"), *(len(row[5]) for row in rows)),
    ]
    print(_format_row(("RUN ID", "AGENT", "ISSUE", "STATUS", "ELAPSED", "LAST LOG"), widths))
    for row in rows:
        print(_format_row(row, widths))
    return 0


def _print_detail(run_dir: Path, run_id: str, *, json_output: bool) -> int:
    status = find_status(run_dir, run_id)
    if status is None:
        print(f"Run not found: {run_id}", file=sys.stderr)
        return 1

    record = status.to_dict()
    if json_output:
        print(json.dumps(record, indent=2))
        return 0

    print(json.dumps(record, indent=2))
    _print_log_tail("stdout", _resolve_log_path(run_dir, status.stdout_log))
    # Legacy `.err.log` runs are handled by RunStatus.from_dict, which maps an
    # old status file's `stderr_log` onto `agent_log`, so the resolved path
    # already points at the existing log regardless of the run's age.
    _print_log_tail("agent", _resolve_log_path(run_dir, status.agent_log))
    return 0


def _format_row(values: tuple[str, str, str, str, str, str], widths: list[int]) -> str:
    return "  ".join(value.ljust(width) for value, width in zip(values, widths))


def _format_elapsed(status: RunStatus) -> str:
    elapsed = status.elapsed_sec
    if elapsed is None and status.is_active:
        try:
            started_at = datetime.fromisoformat(status.started_at)
        except ValueError:
            return "-"
        elapsed = (datetime.now() - started_at).total_seconds()
    if elapsed is None:
        return "-"
    return f"{elapsed:.2f}s"


def _format_last_log(status: RunStatus) -> str:
    if not status.last_log_line:
        return "-"
    line = status.last_log_line
    if len(line) > 30:
        line = line[:27] + "..."
    return line


def _resolve_log_path(run_dir: Path, log_path: str) -> Path:
    path = Path(log_path)
    if path.is_absolute():
        return path
    return run_dir.parent / path


def _print_log_tail(label: str, path: Path) -> None:
    print(f"--- {label} tail ({path}) ---")
    if not path.exists():
        print("Log file not found.")
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-TAIL_LINES:]:
        print(line)
