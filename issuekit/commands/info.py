"""Implementation of the info command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from issuekit.guards.author import guard_dict, read_author_guard
from issuekit.config import load_config
from issuekit.core import issue_dict
from issuekit.issue_display import dependency_detail_lines, dependency_marker
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
        "enabledAgents": [name for name, _run_config in config.agents],
        "disabledAgents": list(config.disabled_agents),
        "machineConfigPath": (
            str(config.machine_config_path) if config.machine_config_path is not None else None
        ),
        "repoConfigSource": config.repo_config_source,
        "agentConfigs": {
            name: {
                "binary": run_config.binary,
                "model": run_config.model,
                "reasoningEffort": run_config.reasoning_effort,
                "approvalFlag": run_config.approval_flag,
                "approvalValue": run_config.approval_value,
                "headlessArgv": list(run_config.headless_argv),
                "modelPromptKeys": [model for model, _prompt in run_config.model_prompts],
            }
            for name, run_config in config.agents
        },
        "activeIssues": [
            issue_dict(issue)
            | {
                "priority": issue.priority or None,
                "stage": issue.stage or None,
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
    print(f"- Machine config: {summary['machineConfigPath'] or '-'}")
    print(f"- Repository config: {summary['repoConfigSource']}")
    if summary["authorGuard"]:
        guard = summary["authorGuard"]
        print(
            f"- Author guard: STOP_NOW {guard['kind']} {guard.get('ref') or guard.get('id')}"
        )

    print()
    print("Agent config")
    for name, agent_config in summary["agentConfigs"].items():
        approval_value = agent_config["approvalValue"]
        approval_value_display = (
            f" approval_value={approval_value}" if approval_value is not None else ""
        )
        print(
            f"- {name}: binary={agent_config['binary']} model={agent_config['model'] or '-'} "
            f"reasoning_effort={agent_config['reasoningEffort'] or '-'} "
            f"approval_flag={agent_config['approvalFlag'] or '-'}{approval_value_display}"
        )

    if summary["disabledAgents"]:
        print()
        print("Disabled agents")
        for name in summary["disabledAgents"]:
            print(f"- {name}")

    if summary["activeIssues"]:
        print()
        print("Active issues")
        for issue in active_issues:
            status_display = (
                f"{issue.issue_status}, stage={issue.stage}"
                if issue.stage
                else issue.issue_status
            )
            dependency_status = dependency_marker(issue)
            marker = f" {dependency_status}" if dependency_status else ""
            print(f"- #{issue.id}: {issue.title} [{status_display}] ({issue.ref}){marker}")
            for line in dependency_detail_lines(issue):
                print(f"  {line}")

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
