"""Implementation of the info command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from issuekit.author_guard import guard_dict, read_author_guard
from issuekit.config import load_config
from issuekit.proposals_api import api_client
from issuekit.store import get_store


def register(subparsers: argparse._SubParsersAction) -> None:
    info_parser = subparsers.add_parser("info", help="Show issue tracker status.")
    info_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    info_parser.set_defaults(func=run)


def run(args) -> int:
    config = load_config(Path.cwd())
    with get_store(config) as store:
        active_issues = store.find_for()
        completed_count = store.count_issues(status="completed", include_completed=True)
        latest_completed_id = store.latest_issue_id(
            status="completed",
            include_completed=True,
            total=completed_count,
        )
    incoming_proposals = _incoming_proposals(config)
    author_guard = read_author_guard(Path.cwd())
    summary = {
        "counts": {
            "active": len(active_issues),
            "completed": completed_count,
            "total": len(active_issues) + completed_count,
        },
        "latestCompletedId": latest_completed_id,
        "worker": config.worker_key(),
        "workerPresent": config.worker is not None,
        "activeIssues": [
            {
                "id": issue.id,
                "title": issue.title,
                "priority": issue.priority or None,
                "status": issue.issue_status,
                "stage": issue.stage or None,
                "ref": issue.ref,
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
        "authorGuard": guard_dict(author_guard),
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("Issue tracker status")
    print(f"- Active issues: {summary['counts']['active']}")
    print(f"- Completed issues: {summary['counts']['completed']}")
    print(f"- Total issues: {summary['counts']['total']}")
    print(f"- Latest completed id: {summary['latestCompletedId']}")
    print(f"- Incoming proposals: {len(summary['incomingProposals'])}")
    print(f"- Worker: {summary['worker'] or '-'}")
    if summary["authorGuard"]:
        guard = summary["authorGuard"]
        print(
            f"- Author guard: STOP_NOW {guard['kind']} {guard.get('ref') or guard.get('id')}"
        )

    if summary["activeIssues"]:
        print()
        print("Active issues")
        for issue in summary["activeIssues"]:
            status_display = f"{issue['status']}, stage={issue['stage']}" if issue.get('stage') else issue['status']
            print(f"- #{issue['id']}: {issue['title']} [{status_display}] ({issue['ref']})")

    if summary["incomingProposals"]:
        print()
        print("Incoming proposals")
        for proposal in summary["incomingProposals"]:
            print(f"- #{proposal['id']} {proposal['origin']}: {proposal['title']}")

    return 0


def _incoming_proposals(config) -> list[dict]:
    if not config.api_url:
        return []
    with api_client(config) as client:
        return client.list_proposals(status="pending")
