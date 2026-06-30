"""Shared issue tracker primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


VALID_ISSUE_STATUSES = {"active", "planned", "investigating", "in_progress", "completed"}
VALID_ISSUE_PRIORITIES = {"high", "medium", "low"}
WORKFLOW_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MANAGED_FRONTMATTER_KEYS = {
    "id",
    "status",
    "priority",
    "created",
    "completed",
    "assignee",
    "stage",
    "implementer",
    "author",
    "title",
}
MOJIBAKE_PATTERN = re.compile(
    "\u7e67|\u7e3a|\u8b41|\u8373|\u87b3|\u8708|\u9ae2|\ufffd"
)
NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7F]")


@dataclass(frozen=True)
class Frontmatter:
    data: dict[str, str]
    body: str
    has_frontmatter: bool


@dataclass(frozen=True)
class Issue:
    id: int | None
    file_name_id: int | None
    file_name: str
    file_path: Path
    relative_path: str
    title: str
    status: str
    issue_status: str
    created: str
    completed: str
    priority: str
    assignee: str
    stage: str
    implementer: str
    author: str
    content: str
    frontmatter: Frontmatter
    decode_error: bool = False


def issue_dict(issue: "Issue", *, include_body: bool = False) -> dict[str, object]:
    """Serialize an issue for JSON output.

    Shared by the MCP server and the CLI so both paths emit identical payloads.
    """
    data: dict[str, object] = {
        "id": issue.id,
        "title": issue.title,
        "status": issue.issue_status,
        "assignee": issue.assignee,
        "stage": issue.stage,
        "implementer": issue.implementer,
        "author": issue.author,
        "file": issue.relative_path,
    }
    if include_body:
        data["body"] = issue.frontmatter.body
    return data


def _drop_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def parse_issue_id(file_name: str) -> int | None:
    match = re.match(r"^(\d+)_.*\.md$", file_name)
    return int(match.group(1)) if match else None


def parse_issue_id_arg(raw_issue_id: str) -> int:
    try:
        return int(raw_issue_id)
    except ValueError as exc:
        raise ValueError(f"Invalid issue id: {raw_issue_id}") from exc


def find_issue_by_id(issues: list[Issue], issue_id: int) -> Issue | None:
    return next((issue for issue in issues if issue.id == issue_id), None)


def parse_issue_frontmatter(content: str) -> Frontmatter:
    if content.startswith("\ufeff"):
        content = content[1:]

    if not (content.startswith("---\n") or content.startswith("---\r\n")):
        return Frontmatter(data={}, body=content, has_frontmatter=False)

    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", content)
    if not match:
        return Frontmatter(data={}, body=content, has_frontmatter=False)

    data: dict[str, str] = {}
    for raw_line in re.split(r"\r?\n", match.group(1)):
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if (
            len(value) >= 2
            and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"))
        ):
            value = value[1:-1]
        data[key.strip()] = value

    return Frontmatter(data=data, body=content[match.end() :], has_frontmatter=True)


def parse_frontmatter_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def get_issue_heading(content: str) -> re.Match[str] | None:
    return re.search(r"^#\s+Issue\s+#\d+:\s*(.+)$", content, re.MULTILINE) or re.search(
        r"^#\s+(.+)$", content, re.MULTILINE
    )


def read_issues(issues_dir: Path | str, directory_status: str) -> list[Issue]:
    issues_path = Path(issues_dir)
    source_dir = issues_path / directory_status
    if not source_dir.exists():
        return []

    issues: list[Issue] = []
    for file_path in source_dir.iterdir():
        if file_path.suffix != ".md":
            continue
        file_name = file_path.name
        file_name_id = parse_issue_id(file_name)
        try:
            content = file_path.read_bytes().decode("utf-8-sig")
            decode_error = False
        except UnicodeDecodeError:
            content = ""
            decode_error = True
        frontmatter = parse_issue_frontmatter(content)
        metadata = frontmatter.data
        issue_id = parse_frontmatter_id(metadata.get("id")) or file_name_id
        heading = get_issue_heading(frontmatter.body)
        heading_title = _clean_title(heading.group(1)) if heading else ""
        title = (
            _normalize(metadata.get("title"))
            or (heading_title if heading_title and not MOJIBAKE_PATTERN.search(heading_title) else "")
            or _title_from_file_name(file_name)
        )
        relative_path = file_path.relative_to(issues_path).as_posix()
        issues.append(
            Issue(
                id=issue_id,
                file_name_id=file_name_id,
                file_name=file_name,
                file_path=file_path,
                relative_path=relative_path,
                title=title,
                status=directory_status,
                issue_status=_normalize(metadata.get("status")) or directory_status,
                created=_normalize(metadata.get("created")),
                completed=_normalize(metadata.get("completed")),
                priority=_normalize(metadata.get("priority")),
                assignee=_normalize(metadata.get("assignee")),
                stage=_normalize(metadata.get("stage")),
                implementer=_normalize(metadata.get("implementer")),
                author=_normalize(metadata.get("author")),
                content=content,
                frontmatter=frontmatter,
                decode_error=decode_error,
            )
        )

    return sorted(issues, key=lambda issue: (issue.id or 0, issue.file_name))


def read_active_issues(issues_dir: Path | str) -> list[Issue]:
    """Read issues from the active tracker directory."""
    return read_issues(issues_dir, "active")


def read_completed_issues(issues_dir: Path | str) -> list[Issue]:
    """Read issues from the completed tracker directory."""
    return read_issues(issues_dir, "completed")


def read_all_issues(issues_dir: Path | str) -> tuple[list[Issue], list[Issue], list[Issue]]:
    active_issues = read_active_issues(issues_dir)
    completed_issues = read_completed_issues(issues_dir)
    return active_issues, completed_issues, [*active_issues, *completed_issues]


def has_mojibake(text: str) -> bool:
    return bool(MOJIBAKE_PATTERN.search(text))


def has_non_ascii(text: str) -> bool:
    return bool(NON_ASCII_PATTERN.search(text))


def is_valid_workflow_token(value: str) -> bool:
    return value == "" or bool(WORKFLOW_TOKEN_PATTERN.fullmatch(value))


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _normalize(value: object) -> str:
    return "" if value is None else str(value).strip()


def slugify(
    value: str,
    *,
    default: str,
    max_len: int | None = None,
) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if max_len is not None:
        slug = slug[:max_len]
    return slug or default


def _title_from_file_name(file_name: str) -> str:
    return re.sub(r"\.md$", "", re.sub(r"^\d+_", "", file_name))
