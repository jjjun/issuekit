"""Implementation of the author command."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

from issuekit.commands import generate_indexes, validate
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    VALID_ISSUE_PRIORITIES,
    format_issue_frontmatter,
    get_next_issue_id,
    has_non_ascii,
    is_valid_workflow_token,
    slugify as _core_slugify,
    read_all_issues,
    write_issue_atomic,
)
from issuekit.workflow import WorkflowError


def run(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())

    try:
        authored = author_issue(
            issues_dir,
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            priority=args.priority,
            agent=args.agent,
            assign=args.assign,
            config=config,
        )
    except (OSError, UnicodeError, ValueError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if config.use_filesystem_store:
        generate_indexes.write_index_files(issues_dir, config.recent_count)
    validate_result = validate.run(args)
    if validate_result != 0:
        return validate_result

    print(f"Authored issue: {_authored_ref(authored, issues_dir)}")
    return 0


def author_issue(
    issues_dir: Path | str,
    *,
    title: str,
    body: str | None,
    body_file: str | None,
    priority: str,
    agent: str,
    assign: str | None = None,
    config: IssuekitConfig | None = None,
) -> Path | Issue:
    config = config or IssuekitConfig()
    _validate_author_input(
        title=title,
        priority=priority,
        agent=agent,
        assign=assign,
        config=config,
    )
    issue_body = _read_body(body=body, body_file=body_file)
    if has_non_ascii(issue_body):
        raise ValueError("--body and --body-file must be ASCII-only.")
    if not config.use_filesystem_store:
        from issuekit.store import get_store

        store = get_store(config, issues_dir)
        return store.create_issue(  # type: ignore[attr-defined]
            title=title.strip(),
            body=issue_body.strip(),
            priority=priority,
            author=agent,
            assignee=assign,
        )

    issues_path = Path(issues_dir)
    _, _, all_issues = read_all_issues(issues_path)
    issue_id = get_next_issue_id(all_issues)
    slug = _slugify(title)
    issue_path = issues_path / "active" / f"{issue_id:03d}_{slug}.md"
    if issue_path.exists():
        raise WorkflowError(f"Issue file already exists: {issue_path}")

    frontmatter = format_issue_frontmatter(
        {
            "id": issue_id,
            "status": "active",
            "priority": priority,
            "created": date.today().isoformat(),
            "completed": "",
            "assignee": assign or "",
            "stage": "todo",
            "implementer": "",
            "author": agent,
            "title": title.strip(),
        }
    )
    content = f"{frontmatter}# Issue #{issue_id}: {title.strip()}\n\n{issue_body.strip()}\n"
    write_issue_atomic(issue_path, content)
    return issue_path


def _validate_author_input(
    *,
    title: str,
    priority: str,
    agent: str,
    assign: str | None,
    config: IssuekitConfig,
) -> None:
    if not title.strip():
        raise ValueError("--title is required.")
    if has_non_ascii(title):
        raise ValueError("--title must be ASCII-only.")
    if priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}")
    _validate_agent_token(agent, "--agent", config)
    if assign:
        _validate_agent_token(assign, "--assign", config)


def _validate_agent_token(value: str, label: str, config: IssuekitConfig) -> None:
    if not is_valid_workflow_token(value):
        raise WorkflowError(f"Invalid {label} token: {value}")
    if value not in config.assignees:
        raise WorkflowError(f"Unknown {label}: {value}")


def _read_body(*, body: str | None, body_file: str | None) -> str:
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    raise ValueError("--body or --body-file is required.")


def _slugify(title: str) -> str:
    return _core_slugify(title.strip(), default="issue")


def _authored_ref(authored: Path | Issue, issues_dir: Path) -> str:
    if isinstance(authored, Issue):
        return authored.relative_path
    return authored.relative_to(issues_dir).as_posix()
