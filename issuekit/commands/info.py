"""Implementation of the info command."""

from __future__ import annotations

import json
from pathlib import Path

from issuekit.config import load_config
from issuekit.core import build_index_files, get_next_issue_id, group_issues_by_id, read_all_issues, read_index_files


def run(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    active_issues, completed_issues, all_issues = read_all_issues(issues_dir)
    expected_indexes = build_index_files(active_issues, completed_issues, config.recent_count)
    actual_index_names = read_index_files(issues_dir)
    duplicate_ids = [
        {
            "id": issue_id,
            "known": False,
            "files": [issue.relative_path for issue in group],
        }
        for issue_id, group in group_issues_by_id(all_issues).items()
        if len(group) > 1
    ]
    missing_indexes = [name for name in expected_indexes if name not in actual_index_names]
    extra_indexes = [name for name in actual_index_names if name not in expected_indexes]
    stale_indexes = _stale_indexes(issues_dir, expected_indexes, actual_index_names)
    summary = {
        "counts": {
            "active": len(active_issues),
            "completed": len(completed_issues),
            "total": len(all_issues),
        },
        "nextIssueId": get_next_issue_id(all_issues),
        "latestCompletedId": max((issue.id or 0 for issue in completed_issues), default=0),
        "activeIssues": [
            {
                "id": issue.id,
                "title": issue.title,
                "priority": issue.priority or None,
                "status": issue.issue_status or issue.status,
                "file": issue.relative_path,
            }
            for issue in active_issues
        ],
        "duplicateIds": duplicate_ids,
        "indexes": {
            "expected": list(expected_indexes),
            "missing": missing_indexes,
            "extra": extra_indexes,
            "stale": stale_indexes,
            "ok": not missing_indexes and not extra_indexes and not stale_indexes,
        },
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("Issue tracker status")
    print(f"- Active issues: {summary['counts']['active']}")
    print(f"- Completed issues: {summary['counts']['completed']}")
    print(f"- Total issue files: {summary['counts']['total']}")
    print(f"- Next issue id: {summary['nextIssueId']}")
    print(f"- Latest completed id: {summary['latestCompletedId']}")
    print(f"- Indexes: {'ok' if summary['indexes']['ok'] else 'needs regeneration'}")

    if summary["activeIssues"]:
        print()
        print("Active issues")
        for issue in summary["activeIssues"]:
            print(f"- #{issue['id']}: {issue['title']} [{issue['status']}] ({issue['file']})")

    if duplicate_ids:
        print()
        print("Duplicate ids")
        for duplicate in duplicate_ids:
            print(f"- #{duplicate['id']}: unexpected duplicate")
            for file in duplicate["files"]:
                print(f"  - {file}")

    if not summary["indexes"]["ok"]:
        print()
        print("Index mismatches")
        for name in missing_indexes:
            print(f"- Missing: {name}")
        for name in extra_indexes:
            print(f"- Extra: {name}")
        for name in stale_indexes:
            print(f"- Stale: {name}")
        print()
        print("Run: issuekit generate-indexes")

    return 0


def _stale_indexes(issues_dir: Path, expected_indexes: dict[str, str], actual_names: list[str]) -> list[str]:
    stale = []
    indexes_dir = issues_dir / "indexes"
    for name, expected_content in expected_indexes.items():
        if name not in actual_names:
            continue
        if (indexes_dir / name).read_text(encoding="utf-8-sig") != expected_content:
            stale.append(name)
    return stale
