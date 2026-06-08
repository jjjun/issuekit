"""Implementation of the info command."""

from __future__ import annotations

import json
from pathlib import Path

from issuekit.config import load_config
from issuekit.core import (
    build_index_files,
    diff_index_files,
    get_next_issue_id,
    group_issues_by_id,
    read_all_issues,
)
from issuekit.proposals import list_incoming


def run(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    active_issues, completed_issues, all_issues = read_all_issues(issues_dir)
    incoming_proposals = list_incoming(issues_dir)
    expected_indexes = build_index_files(active_issues, completed_issues, config.recent_count)
    index_diff = diff_index_files(issues_dir, expected_indexes)
    duplicate_ids = [
        {
            "id": issue_id,
            "known": False,
            "files": [issue.relative_path for issue in group],
        }
        for issue_id, group in group_issues_by_id(all_issues).items()
        if len(group) > 1
    ]
    missing_indexes = index_diff.missing
    extra_indexes = index_diff.extra
    stale_indexes = index_diff.stale
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
                "stage": issue.stage or None,
                "file": issue.relative_path,
            }
            for issue in active_issues
        ],
        "incomingProposals": [
            {
                "origin": proposal.origin,
                "title": proposal.title,
                "created": proposal.created,
                "file": _proposal_relative_path(issues_dir, proposal.file_path),
            }
            for proposal in incoming_proposals
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
    print(f"- Incoming proposals: {len(summary['incomingProposals'])}")
    print(f"- Indexes: {'ok' if summary['indexes']['ok'] else 'needs regeneration'}")

    if summary["activeIssues"]:
        print()
        print("Active issues")
        for issue in summary["activeIssues"]:
            status_display = f"{issue['status']}, stage={issue['stage']}" if issue.get('stage') else issue['status']
            print(f"- #{issue['id']}: {issue['title']} [{status_display}] ({issue['file']})")

    if summary["incomingProposals"]:
        print()
        print("Incoming proposals")
        for proposal in summary["incomingProposals"]:
            print(f"- {proposal['origin']}: {proposal['title']} ({proposal['file']})")

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


def _proposal_relative_path(issues_dir: Path, proposal_path: Path | None) -> str:
    if proposal_path is None:
        return ""
    return proposal_path.relative_to(issues_dir).as_posix()
