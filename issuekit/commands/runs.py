"""Implementation of the runs command."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from issuekit.agentrun.logtail import tail_lines
from issuekit.agentrun.run_dir import prepare_run_dir
from issuekit.agentrun.status import (
    RUN_ID_PATTERN,
    RunStatus,
    find_status,
    list_statuses,
    reconcile_stale,
    status_path,
)
from issuekit.commands._common import add_json_flag, print_json

TAIL_LINES = 40


def register(subparsers: argparse._SubParsersAction) -> None:
    runs_parser = subparsers.add_parser(
        "runs",
        help="List and inspect agent runs.",
    )
    runs_parser.add_argument("run_id", nargs="?", help="Run id to inspect.")
    runs_parser.add_argument(
        "--active",
        action="store_true",
        help="Show only running agent runs.",
    )
    add_json_flag(runs_parser)
    runs_parser.set_defaults(func=run)


def run(args) -> int:
    run_dir = Path.cwd() / ".agent-runs"
    if args.run_id and RUN_ID_PATTERN.fullmatch(args.run_id) is None:
        print(f"Invalid run id: {args.run_id}", file=sys.stderr)
        return 1
    if run_dir.exists() or run_dir.is_symlink():
        try:
            run_dir = prepare_run_dir(run_dir.parent, run_dir)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.run_id:
        return _print_detail(run_dir, args.run_id, json_output=args.json)
    return _print_list(run_dir, active_only=args.active, json_output=args.json)


def _print_list(run_dir: Path, *, active_only: bool, json_output: bool) -> int:
    statuses = list_statuses(run_dir) if run_dir.exists() else []
    statuses = [
        reconcile_stale(
            run_dir,
            status,
            status_file=status_path(run_dir, status.run_id),
        )
        for status in statuses
    ]
    if active_only:
        statuses = [status for status in statuses if status.is_active]

    if json_output:
        print_json([_status_record(run_dir, status) for status in statuses])
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
    try:
        status = find_status(run_dir, run_id)
    except (OSError, ValueError) as exc:
        print(
            f"Run status file is unreadable: {status_path(run_dir, run_id)}: {exc}",
            file=sys.stderr,
        )
        return 1
    if status is None:
        print(f"Run not found: {run_id}", file=sys.stderr)
        return 1
    status_file = status_path(run_dir, run_id)
    status = reconcile_stale(run_dir, status, status_file=status_file)

    stdout_path = _resolve_log_path(run_dir, status.stdout_log)
    agent_path = _resolve_log_path(run_dir, status.agent_log)
    record = _status_record(run_dir, status)
    if json_output:
        print_json(record)
        return 0

    print_json(record)
    _print_log_tail("stdout", stdout_path)
    _print_log_tail("agent", agent_path)
    return 0


def _format_row(values: tuple[str, str, str, str, str, str], widths: list[int]) -> str:
    return "  ".join(value.ljust(width) for value, width in zip(values, widths, strict=True))


def _status_record(run_dir: Path, status: RunStatus) -> dict[str, object]:
    record = status.to_dict()
    for field in ("stdout_log", "agent_log"):
        if _resolve_log_path(run_dir, str(record[field])) is None:
            record[field] = "log path outside .agent-runs; not shown"
    return record


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
    line = status.failure_reason or status.last_log_line
    if not line:
        return "-"
    if len(line) > 30:
        line = line[:27] + "..."
    return line


def _resolve_log_path(run_dir: Path, log_path: str) -> Path | None:
    path = Path(log_path)
    if path.is_absolute():
        return None
    run_root = run_dir.parent / ".agent-runs"
    if run_root.is_symlink():
        return None
    resolved = (run_dir.parent / path).resolve()
    try:
        resolved.relative_to(run_root.resolve())
    except ValueError:
        return None
    return resolved


def _print_log_tail(label: str, path: Path | None) -> None:
    if path is None:
        print(f"--- {label} tail ---")
        print("log path outside .agent-runs; not shown")
        return
    print(f"--- {label} tail ({path.name}) ---")
    if not path.exists():
        print("Log file not found.")
        return
    for line in tail_lines(path, TAIL_LINES):
        print(line)
