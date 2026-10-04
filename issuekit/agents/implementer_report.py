"""Build implementer submission reports and summarize agent failures."""

from __future__ import annotations

from pathlib import Path

from issuekit.agentrun import AgentResult
from issuekit.agentrun.parsed import parsed_int, parsed_is_error, parsed_usage
from issuekit.agentrun.status import read_status
from issuekit.agents.handoff import NO_IMPLEMENTATION_CHANGES_MARKER
from issuekit.encoding import sanitize_to_ascii
from issuekit.paths import display_path as format_display_path

MAX_IMPLEMENTER_REPORT_CHARS = 4000


def submission_summary(
    prefix: str,
    result: AgentResult,
    cwd: Path,
    *,
    no_implementation_changes: bool = False,
) -> str:
    run_log = sanitize_to_ascii(display_path(result.stdout_path, cwd))
    summary = f"{prefix}\nRun log: `{run_log}`"
    if no_implementation_changes:
        summary = f"{summary}\n{NO_IMPLEMENTATION_CHANGES_MARKER}"
    if result.report_path is None or not result.report_path.is_file():
        return summary
    try:
        with result.report_path.open(encoding="utf-8", errors="replace") as stream:
            raw_report = stream.read(MAX_IMPLEMENTER_REPORT_CHARS + 1)
    except OSError:
        return summary
    report = sanitize_to_ascii(raw_report).strip()
    if not report:
        return summary

    truncation_marker = "\n[Implementer report truncated; see run log.]"
    if (
        len(raw_report) > MAX_IMPLEMENTER_REPORT_CHARS
        or len(report) > MAX_IMPLEMENTER_REPORT_CHARS
    ):
        report = (
            report[: MAX_IMPLEMENTER_REPORT_CHARS - len(truncation_marker)].rstrip()
            + truncation_marker
        )
    return f"{summary}\n\nImplementer report:\n{report}"


def has_report_content(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(text.strip())


def display_path(path: Path, cwd: Path) -> str:
    return format_display_path(path, cwd)


def diagnostic_log_detail(result: AgentResult) -> str:
    if result.status_path is None:
        return ""
    try:
        status = read_status(result.status_path)
    except (OSError, ValueError):
        return ""
    if status.failure_reason:
        return f" Agent failure reason: {status.failure_reason}"
    if status.last_log_line:
        return f" Last agent log line: {status.last_log_line}"
    return ""


def is_startup_failure(result: AgentResult) -> bool:
    parsed = result.parsed or {}
    if parsed_is_error(parsed) is not True:
        return False
    num_turns = parsed_int(parsed, "num_turns")
    if num_turns is not None and num_turns <= 1:
        return True
    usage_values = parsed_usage(parsed).values()
    return not usage_values or all(value == 0 for value in usage_values)
