"""Implementation of the validate command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.core import (
    GENERATED_FILE_MARKER,
    VALID_ISSUE_PRIORITIES,
    VALID_ISSUE_STATUSES,
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


def run(_args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    active_issues, completed_issues, issues = read_all_issues(issues_dir)
    errors: list[str] = []
    warnings: list[str] = []

    for issue in issues:
        if issue.decode_error:
            errors.append(f"Issue file is not valid UTF-8: {issue.relative_path}")
            continue

        if issue.id is None:
            errors.append(f"Issue file does not start with a numeric id: {issue.relative_path}")

        if (
            issue.id is not None
            and issue.id >= config.ascii_id_threshold
            and has_non_ascii(issue.content)
        ):
            errors.append(
                "Issue files with id "
                f"{config.ascii_id_threshold} or newer must use English ASCII-only text: "
                f"{issue.relative_path}"
            )

        if issue.frontmatter.has_frontmatter:
            metadata = issue.frontmatter.data
            metadata_id = parse_frontmatter_id(metadata.get("id"))
            if metadata_id is None:
                errors.append(f"Issue frontmatter is missing a numeric id: {issue.relative_path}")
            elif issue.file_name_id is not None and metadata_id != issue.file_name_id:
                errors.append(
                    f"Issue frontmatter id {metadata_id} does not match filename id "
                    f"{issue.file_name_id}: {issue.relative_path}"
                )

            if metadata.get("status") not in VALID_ISSUE_STATUSES:
                errors.append(
                    f"Issue frontmatter has invalid status \"{metadata.get('status', '')}\": "
                    f"{issue.relative_path}"
                )

            if metadata.get("priority") not in VALID_ISSUE_PRIORITIES:
                errors.append(
                    f"Issue frontmatter has invalid priority \"{metadata.get('priority', '')}\": "
                    f"{issue.relative_path}"
                )

            assignee = metadata.get("assignee", "")
            if assignee:
                if not is_valid_workflow_token(assignee):
                    errors.append(
                        f"Issue frontmatter has invalid assignee token \"{assignee}\": "
                        f"{issue.relative_path}"
                    )
                if assignee not in config.assignees:
                    errors.append(
                        f"Issue frontmatter has unknown assignee \"{assignee}\": "
                        f"{issue.relative_path}"
                    )

            stage = metadata.get("stage", "")
            if stage:
                if not is_valid_workflow_token(stage):
                    errors.append(
                        f"Issue frontmatter has invalid stage token \"{stage}\": "
                        f"{issue.relative_path}"
                    )
                if stage not in config.stages:
                    errors.append(
                        f"Issue frontmatter has unknown stage \"{stage}\": {issue.relative_path}"
                    )

            if issue.status == "completed" and metadata.get("status") != "completed":
                errors.append(
                    f"Completed issue frontmatter status must be \"completed\": {issue.relative_path}"
                )

            if issue.status == "active" and metadata.get("status") == "completed":
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

        heading = get_issue_heading(issue.frontmatter.body)
        if issue.frontmatter.has_frontmatter and heading and has_mojibake(heading.group(1)):
            warnings.append(f"Issue heading contains likely mojibake: {issue.relative_path}")

    for issue_id, group in group_issues_by_id(issues).items():
        if len(group) <= 1:
            continue
        files = ", ".join(issue.relative_path for issue in group)
        errors.append(f"Issue id {issue_id} is used by {files}")

    index_files = read_index_files(issues_dir)
    expected_indexes = build_index_files(active_issues, completed_issues, config.recent_count)
    for name in expected_indexes:
        if name not in index_files:
            errors.append(f"Missing generated index: docs/issues/indexes/{name}")
    for name in index_files:
        if name not in expected_indexes:
            errors.append(f"Unexpected generated index: docs/issues/indexes/{name}")

    indexes_dir = issues_dir / "indexes"
    for name in index_files:
        content = (indexes_dir / name).read_text(encoding="utf-8-sig")
        if GENERATED_FILE_MARKER not in content:
            errors.append(f"Index file is missing generated-file marker: docs/issues/indexes/{name}")
        expected_content = expected_indexes.get(name)
        if expected_content is not None and content != expected_content:
            errors.append(f"Generated index is stale: docs/issues/indexes/{name}")

    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Issue validation passed ({len(issues)} files, {len(warnings)} warnings).")
    return 0
