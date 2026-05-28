"""Implementation of the complete command."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

from issuekit.commands import generate_indexes, validate
from issuekit.config import load_config
from issuekit.core import format_issue_frontmatter, has_non_ascii, parse_issue_frontmatter, read_all_issues


def run(args) -> int:
    summary = args.summary or ""
    verification = args.verification or ""
    if has_non_ascii(summary) or has_non_ascii(verification):
        print("--summary and --verification must be ASCII-only.", file=sys.stderr)
        return 1

    try:
        issue_id = int(args.id)
    except ValueError:
        print(f"Invalid issue id: {args.id}", file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    active_issues, _, _ = read_all_issues(issues_dir)
    issue = next((candidate for candidate in active_issues if candidate.id == issue_id), None)
    if issue is None:
        print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
        return 1
    if issue.decode_error:
        print(f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}", file=sys.stderr)
        return 1

    completed_date = date.today().isoformat()
    frontmatter = parse_issue_frontmatter(issue.content)
    data = {
        "id": issue.id,
        "status": "completed",
        "priority": issue.priority or "medium",
        "created": issue.created or completed_date,
        "completed": completed_date,
        "title": issue.title,
    }
    next_content = format_issue_frontmatter(data) + _append_completion_note(
        frontmatter.body,
        summary=summary,
        verification=verification,
        completed_date=completed_date,
    )
    completed_dir = issues_dir / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    completed_path = completed_dir / issue.file_name

    issue.file_path.write_text(next_content, encoding="utf-8", newline="\n")
    issue.file_path.replace(completed_path)
    generate_indexes.write_index_files(issues_dir, config.recent_count)
    validate_result = validate.run(args)
    if validate_result != 0:
        return validate_result

    print(f"Completed issue #{issue.id}: {completed_path.relative_to(Path.cwd()).as_posix()}")
    return 0


def _append_completion_note(
    body: str,
    *,
    summary: str,
    verification: str,
    completed_date: str,
) -> str:
    lines = [
        "",
        f"**Completed**: {completed_date}",
        "",
        "## Completion Notes",
        "",
    ]
    lines.append(f"- {summary}" if summary else "- Completed the tracked scope.")
    if verification:
        lines.append(f"- Verification: `{verification}`")
    note = "\n".join(lines)
    return f"{body.rstrip()}\n{note}\n"
