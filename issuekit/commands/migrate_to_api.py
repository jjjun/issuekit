"""Import legacy docs/issues Markdown issues into the API backend."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from issuekit.client import IssuekitClient
from issuekit.config import load_config
from issuekit.core import (
    MANAGED_FRONTMATTER_KEYS,
    Issue,
    parse_frontmatter_id,
    read_all_issues,
)
from issuekit.workflow import WorkflowError


EXPLICIT_IMPORT_KEYS = MANAGED_FRONTMATTER_KEYS | {"origin", "reviewer"}


def run(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = Path(args.issues_dir) if args.issues_dir else config.issues_path(Path.cwd())
    try:
        payload = build_import_payload(issues_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"Dry run: built import payload for {len(payload)} issue(s) "
            f"from {issues_dir.as_posix()}."
        )
        return 0

    if not config.api_url:
        print(
            "migrate-to-api requires api_url in issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL.",
            file=sys.stderr,
        )
        return 1

    try:
        client = IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
        )
        client.import_issues(payload)
        server_issues = client.list_issues()
        verify_import(payload, server_issues)
    except (WorkflowError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(locals().get("client"), "close", None)
        if callable(close):
            close()

    print(f"Migrated {len(payload)} issue(s) to project {config.project}.")
    return 0


def build_import_payload(issues_dir: Path | str) -> list[dict[str, Any]]:
    active_issues, completed_issues, all_issues = read_all_issues(issues_dir)
    _validate_source_issues(all_issues)
    payload = [_issue_payload(issue) for issue in [*active_issues, *completed_issues]]
    return sorted(payload, key=lambda item: int(item["number"]))


def verify_import(source_payload: list[dict[str, Any]], server_issues: list[dict[str, Any]]) -> None:
    source_ids = {int(issue["number"]) for issue in source_payload}
    server_ids = {_server_issue_id(issue) for issue in server_issues}
    missing = sorted(source_ids - server_ids)
    if missing:
        joined = ", ".join(str(issue_id) for issue_id in missing)
        raise ValueError(f"Imported issue id(s) missing from server list: {joined}")
    if len(source_ids) != len(source_payload):
        raise ValueError("Source payload contains duplicate issue ids.")
    if len(source_ids) > len(server_ids):
        raise ValueError(
            f"Server returned fewer issues than source payload ({len(server_ids)} < {len(source_ids)})."
        )


def _validate_source_issues(issues: list[Issue]) -> None:
    seen: dict[int, str] = {}
    for issue in issues:
        if issue.decode_error:
            raise ValueError(f"Issue file is not valid UTF-8: {issue.relative_path}")
        if issue.id is None:
            raise ValueError(f"Issue file is missing an id: {issue.relative_path}")
        previous = seen.get(issue.id)
        if previous is not None:
            raise ValueError(f"Issue id {issue.id} is used by {previous}, {issue.relative_path}")
        seen[issue.id] = issue.relative_path


def _issue_payload(issue: Issue) -> dict[str, Any]:
    data = issue.frontmatter.data
    issue_id = parse_frontmatter_id(data.get("id")) or issue.id
    if issue_id is None:
        raise ValueError(f"Issue file is missing an id: {issue.relative_path}")
    payload: dict[str, Any] = {
        "number": issue_id,
        "title": data.get("title") or issue.title,
        "body": issue.frontmatter.body.lstrip("\n"),
        "status": data.get("status") or issue.issue_status or issue.status,
        "priority": data.get("priority") or issue.priority or "medium",
        "stage": data.get("stage") or issue.stage,
        "assignee": data.get("assignee") or issue.assignee,
        "implementer": data.get("implementer") or issue.implementer,
        "author": data.get("author") or issue.author,
        "reviewer": data.get("reviewer", ""),
        "created": data.get("created") or issue.created,
        "completed": data.get("completed") or issue.completed,
        "origin": data.get("origin", ""),
        "extra": {
            key: value
            for key, value in data.items()
            if key not in EXPLICIT_IMPORT_KEYS
        },
    }
    return payload


def _server_issue_id(issue: dict[str, Any]) -> int:
    raw = issue.get("id", issue.get("number"))
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Server issue response has invalid id: {raw}") from exc
