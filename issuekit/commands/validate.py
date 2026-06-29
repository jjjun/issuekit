"""Implementation of the validate command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.core import (
    GENERATED_FILE_MARKER,
    diff_index_files,
    VALID_ISSUE_PRIORITIES,
    VALID_ISSUE_STATUSES,
    Issue,
    build_index_files,
    get_issue_heading,
    group_issues_by_id,
    has_mojibake,
    has_non_ascii,
    is_valid_workflow_token,
    parse_frontmatter_id,
    read_all_issues,
    read_index_files,
)
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


def run(_args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    if config.api_url:
        return _run_api_validation(config, issues_dir)

    active_issues, completed_issues, issues = read_all_issues(issues_dir)

    errors: list[str] = []
    warnings: list[str] = []

    for issue in issues:
        errors.extend(_collect_issue_errors(issue, config))
        warnings.extend(_collect_issue_warnings(issue))

    errors.extend(_collect_duplicate_id_errors(issues))
    errors.extend(
        _collect_index_errors(
            issues_dir,
            active_issues,
            completed_issues,
            config.recent_count,
        )
    )

    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Issue validation passed ({len(issues)} files, {len(warnings)} warnings).")
    return 0


def _run_api_validation(config, issues_dir: Path) -> int:
    try:
        _, _, issues = get_store(config, issues_dir).read_all_issues()
    except (WorkflowError, ValueError) as exc:
        print(f"Error: API validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"API validation passed ({len(issues)} issues).")
    return 0


def _collect_issue_errors(issue: Issue, config) -> list[str]:
    if issue.decode_error:
        return [f"Issue file is not valid UTF-8: {issue.relative_path}"]

    errors: list[str] = []
    if issue.id is None:
        errors.append(f"Issue file does not start with a numeric id: {issue.relative_path}")

    if issue.id is not None and issue.id >= config.ascii_id_threshold and has_non_ascii(issue.content):
        errors.append(
            "Issue files with id "
            f"{config.ascii_id_threshold} or newer must use English ASCII-only text: "
            f"{issue.relative_path}"
        )

    if issue.frontmatter.has_frontmatter:
        errors.extend(_collect_frontmatter_errors(issue, config))

    return errors


def _collect_frontmatter_errors(issue: Issue, config) -> list[str]:
    errors: list[str] = []
    metadata = issue.frontmatter.data
    metadata_id = parse_frontmatter_id(metadata.get("id"))
    if metadata_id is None:
        errors.append(f"Issue frontmatter is missing a numeric id: {issue.relative_path}")
    elif issue.file_name_id is not None and metadata_id != issue.file_name_id:
        errors.append(
            f"Issue frontmatter id {metadata_id} does not match filename id "
            f"{issue.file_name_id}: {issue.relative_path}"
        )

    metadata_status = metadata.get("status", "")
    if metadata_status not in VALID_ISSUE_STATUSES:
        errors.append(
            f"Issue frontmatter has invalid status \"{metadata_status}\": "
            f"{issue.relative_path}"
        )

    metadata_priority = metadata.get("priority", "")
    if metadata_priority not in VALID_ISSUE_PRIORITIES:
        errors.append(
            f"Issue frontmatter has invalid priority \"{metadata_priority}\": "
            f"{issue.relative_path}"
        )

    assignee = metadata.get("assignee", "")
    errors.extend(_collect_workflow_token_errors(assignee, "assignee", "assignee", issue.relative_path, config))
    implementer = metadata.get("implementer", "")
    errors.extend(
        _collect_workflow_token_errors(
            implementer,
            "implementer",
            "implementer",
            issue.relative_path,
            config,
        )
    )
    author = metadata.get("author", "")
    errors.extend(
        _collect_workflow_token_errors(author, "author", "author", issue.relative_path, config)
    )
    stage = metadata.get("stage", "")
    errors.extend(_collect_workflow_token_errors(stage, "stage", "stage", issue.relative_path, config))

    if issue.status == "completed" and metadata_status != "completed":
        errors.append(
            f"Completed issue frontmatter status must be \"completed\": {issue.relative_path}"
        )

    if issue.status == "active" and metadata_status == "completed":
        errors.append(
            f"Active issue frontmatter status must not be \"completed\": {issue.relative_path}"
        )

    if not metadata.get("created"):
        errors.append(f"Issue frontmatter is missing created date: {issue.relative_path}")

    if not metadata.get("title"):
        errors.append(f"Issue frontmatter is missing title: {issue.relative_path}")

    metadata_text = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    if has_mojibake(metadata_text):
        errors.append(f"Issue frontmatter contains likely mojibake: {issue.relative_path}")

    return errors


def _collect_workflow_token_errors(
    token: str,
    token_name: str,
    issue_field: str,
    issue_path: str,
    config,
) -> list[str]:
    errors: list[str] = []
    if not token:
        return errors

    if not is_valid_workflow_token(token):
        errors.append(f"Issue frontmatter has invalid {token_name} token \"{token}\": {issue_path}")

    if issue_field == "stage":
        if token not in config.stages:
            errors.append(f"Issue frontmatter has unknown stage \"{token}\": {issue_path}")
    else:
        if token not in config.assignees:
            errors.append(f"Issue frontmatter has unknown {issue_field} \"{token}\": {issue_path}")

    return errors


def _collect_issue_warnings(issue: Issue) -> list[str]:
    heading = get_issue_heading(issue.frontmatter.body)
    if issue.frontmatter.has_frontmatter and heading and has_mojibake(heading.group(1)):
        return [f"Issue heading contains likely mojibake: {issue.relative_path}"]
    return []


def _collect_duplicate_id_errors(issues: list[Issue]) -> list[str]:
    errors: list[str] = []
    for issue_id, group in group_issues_by_id(issues).items():
        if len(group) <= 1:
            continue
        files = ", ".join(issue.relative_path for issue in group)
        errors.append(f"Issue id {issue_id} is used by {files}")
    return errors


def _collect_index_errors(
    issues_dir: Path,
    active_issues: list[Issue],
    completed_issues: list[Issue],
    recent_count: int,
) -> list[str]:
    errors: list[str] = []
    expected_indexes = build_index_files(active_issues, completed_issues, recent_count)
    index_diff = diff_index_files(issues_dir, expected_indexes)
    for name in index_diff.missing:
        errors.append(f"Missing generated index: docs/issues/indexes/{name}")
    for name in index_diff.extra:
        errors.append(f"Unexpected generated index: docs/issues/indexes/{name}")

    indexes_dir = issues_dir / "indexes"
    for name in read_index_files(issues_dir):
        content = (indexes_dir / name).read_text(encoding="utf-8-sig")
        if GENERATED_FILE_MARKER not in content:
            errors.append(f"Index file is missing generated-file marker: docs/issues/indexes/{name}")

    for name in index_diff.stale:
        errors.append(f"Generated index is stale: docs/issues/indexes/{name}")

    return errors
