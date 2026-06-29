"""Implementation of the info command."""

from __future__ import annotations

import json
from pathlib import Path

from issuekit.commands.propose import _api_client
from issuekit.config import load_config
from issuekit.store import get_store


def run(args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    store = get_store(config, issues_dir)
    active_issues, completed_issues, all_issues = store.read_all_issues()
    incoming_proposals = _incoming_proposals(config)
    summary = {
        "counts": {
            "active": len(active_issues),
            "completed": len(completed_issues),
            "total": len(all_issues),
        },
        "nextIssueId": None,
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
                "id": proposal.get("id"),
                "origin": proposal.get("origin", ""),
                "title": proposal.get("title", ""),
                "created": proposal.get("created"),
            }
            for proposal in incoming_proposals
        ],
        "duplicateIds": [],
        "indexes": None,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("Issue tracker status")
    print(f"- Active issues: {summary['counts']['active']}")
    print(f"- Completed issues: {summary['counts']['completed']}")
    print(f"- Total issues: {summary['counts']['total']}")
    print("- Next issue id: allocated by API")
    print(f"- Latest completed id: {summary['latestCompletedId']}")
    print(f"- Incoming proposals: {len(summary['incomingProposals'])}")

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
            print(f"- #{proposal['id']} {proposal['origin']}: {proposal['title']}")

    return 0


def _incoming_proposals(config) -> list[dict]:
    if not config.api_url:
        return []
    return _api_client(config).list_proposals(status="pending")
