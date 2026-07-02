"""Import legacy docs/issues Markdown issues into the API backend."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import sys
from typing import Any

from issuekit.client import IssuekitClient
from issuekit.config import load_config
from issuekit.legacy_markdown import (
    LegacyIssue,
    MANAGED_FRONTMATTER_KEYS,
    parse_issue_frontmatter,
    parse_frontmatter_id,
    read_all_issues,
)
from issuekit.workflow import WorkflowError


EXPLICIT_IMPORT_KEYS = MANAGED_FRONTMATTER_KEYS | {"origin", "reviewer"}


def register(subparsers: argparse._SubParsersAction) -> None:
    migrate_parser = subparsers.add_parser(
        "migrate-to-api",
        help="Import legacy docs/issues issue files into the API backend.",
    )
    migrate_parser.add_argument(
        "--issues-dir",
        help="Legacy issue directory to import (defaults to configured issues_dir).",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate the import payload without posting it.",
    )
    migrate_parser.set_defaults(func=run)

    migrate_proposals_parser = subparsers.add_parser(
        "migrate-proposals-to-api",
        help="Import legacy docs/issues incoming proposal files into the API backend.",
    )
    migrate_proposals_parser.add_argument(
        "--issues-dir",
        help="Legacy issue directory containing incoming proposals (defaults to configured issues_dir).",
    )
    migrate_proposals_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate the proposal import payload without posting it.",
    )
    migrate_proposals_parser.set_defaults(func=run_proposals)


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
        server_issues = client.list_all_issues() + client.list_all_issues(status="completed")
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


def run_proposals(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = Path(args.issues_dir) if args.issues_dir else config.issues_path(Path.cwd())
    try:
        payload = build_proposal_import_payload(issues_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"Dry run: built proposal import payload for {len(payload)} proposal(s) "
            f"from {issues_dir.as_posix()}."
        )
        return 0

    if not config.api_url:
        print(
            "migrate-proposals-to-api requires api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL.",
            file=sys.stderr,
        )
        return 1

    try:
        client = IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
        )
        stored = client.import_proposals(payload)
        verify_proposal_import(payload, stored)
    except (WorkflowError, ValueError) as exc:
        print(f"Proposal migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(locals().get("client"), "close", None)
        if callable(close):
            close()

    print(f"Migrated {len(payload)} proposal(s) to project {config.project}.")
    return 0


def build_import_payload(issues_dir: Path | str) -> list[dict[str, Any]]:
    active_issues, completed_issues, all_issues = read_all_issues(issues_dir)
    _validate_source_issues(all_issues)
    payload = [_issue_payload(issue) for issue in [*active_issues, *completed_issues]]
    return sorted(payload, key=lambda item: int(item["number"]))


def build_proposal_import_payload(issues_dir: Path | str) -> list[dict[str, Any]]:
    root = Path(issues_dir)
    adopted_numbers = _adopted_issue_numbers(root)
    payload: list[dict[str, Any]] = []
    for status, directory in (
        ("pending", root / "incoming"),
        ("adopted", root / "incoming" / "adopted"),
        ("discarded", root / "incoming" / "discarded"),
    ):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            payload.append(_proposal_import_item(path, status, adopted_numbers))
    return payload


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


def verify_proposal_import(source_payload: list[dict[str, Any]], stored_proposals: list[dict[str, Any]]) -> None:
    duplicate_pending_origins = _duplicate_pending_origins(source_payload)
    if duplicate_pending_origins:
        joined = ", ".join(duplicate_pending_origins)
        raise ValueError(f"Source payload contains duplicate pending proposal origin(s): {joined}")

    source_keys = Counter(_proposal_keys(source_payload))
    stored_keys = Counter(_proposal_keys(stored_proposals))
    missing = sorted(source_keys - stored_keys)
    if missing:
        joined = ", ".join(_format_proposal_key(key) for key in missing)
        raise ValueError(f"Imported proposal(s) missing from response: {joined}")


def _validate_source_issues(issues: list[LegacyIssue]) -> None:
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


def _issue_payload(issue: LegacyIssue) -> dict[str, Any]:
    data = issue.frontmatter.data
    issue_id = parse_frontmatter_id(data.get("id")) or issue.id
    if issue_id is None:
        raise ValueError(f"Issue file is missing an id: {issue.relative_path}")
    status = _first_non_empty(
        data.get("status"),
        issue.issue_status,
        issue.status,
        "active",
    )
    default_stage = "done" if status == "completed" else "todo"
    payload: dict[str, Any] = {
        "number": issue_id,
        "title": data.get("title") or issue.title,
        "body": issue.frontmatter.body.lstrip("\n"),
        "status": status,
        "priority": _first_non_empty(data.get("priority"), issue.priority, "medium"),
        "stage": _first_non_empty(data.get("stage"), issue.stage, default_stage),
        "assignee": data.get("assignee") or issue.assignee,
        "implementer": data.get("implementer") or issue.implementer,
        "author": data.get("author") or issue.author,
        "reviewer": data.get("reviewer", ""),
        "created": _date_or_none(data.get("created"), issue.created),
        "completed": _date_or_none(data.get("completed"), issue.completed),
        "origin": data.get("origin", ""),
        "extra": {
            key: value
            for key, value in data.items()
            if key not in EXPLICIT_IMPORT_KEYS
        },
    }
    return payload


def _proposal_import_item(
    path: Path,
    status: str,
    adopted_numbers: dict[str, int],
) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8-sig")
    frontmatter = parse_issue_frontmatter(content)
    if not frontmatter.has_frontmatter:
        raise ValueError(f"Proposal is missing frontmatter: {path}")
    data = frontmatter.data
    origin = _required(data, "origin", path)
    item: dict[str, Any] = {
        "origin": origin,
        "reply_to": _empty_to_none(data.get("reply_to")),
        "created": _date_or_none(data.get("created")),
        "title": _required(data, "title", path),
        "body": frontmatter.body.strip("\n"),
        "status": status,
        "adopted_issue_number": None,
    }
    if status == "adopted":
        item["adopted_issue_number"] = adopted_numbers.get(origin) or _adopted_number_from_name(path)
    return item


def _adopted_issue_numbers(issues_dir: Path) -> dict[str, int]:
    try:
        _, _, issues = read_all_issues(issues_dir)
    except (OSError, ValueError):
        return {}
    numbers: dict[str, int] = {}
    for issue in issues:
        origin = str(issue.frontmatter.data.get("origin", "")).strip()
        if origin and issue.id is not None:
            numbers[origin] = issue.id
    return numbers


def _adopted_number_from_name(path: Path) -> int | None:
    match = re.match(r"^(\d+)[_-]", path.stem)
    if not match:
        return None
    return int(match.group(1))


def _required(data: dict[str, str], key: str, path: Path) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise ValueError(f"Proposal {path} is missing required field: {key}")
    return value


def _empty_to_none(value: object) -> str | None:
    normalized = "" if value is None else str(value).strip()
    return normalized or None


def _proposal_keys(proposals: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str, str]]:
    return [
        (
            _proposal_value(proposal.get("origin")),
            _proposal_value(proposal.get("status")),
            _proposal_value(proposal.get("title")),
            _proposal_value(proposal.get("body")),
            _proposal_value(proposal.get("reply_to")),
            _proposal_value(proposal.get("adopted_issue_number")),
        )
        for proposal in proposals
    ]


def _duplicate_pending_origins(proposals: list[dict[str, Any]]) -> list[str]:
    origins = Counter(
        _proposal_value(proposal.get("origin"))
        for proposal in proposals
        if _proposal_value(proposal.get("status")) == "pending"
    )
    return sorted(origin for origin, count in origins.items() if origin and count > 1)


def _format_proposal_key(key: tuple[str, str, str, str, str, str]) -> str:
    origin, status, title, *_ = key
    return f"{origin} ({status}): {title}"


def _proposal_value(value: object) -> str:
    return "" if value is None else str(value)


def _first_non_empty(*values: object) -> str:
    for value in values:
        normalized = "" if value is None else str(value).strip()
        if normalized:
            return normalized
    return ""


def _date_or_none(*values: object) -> str | None:
    for value in values:
        normalized = "" if value is None else str(value).strip()
        if normalized:
            return normalized
    return None


def _server_issue_id(issue: dict[str, Any]) -> int:
    raw = issue.get("id", issue.get("number"))
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Server issue response has invalid id: {raw}") from exc
