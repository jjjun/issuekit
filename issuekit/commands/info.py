"""Implementation of the info command."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from issuekit.api import IssuekitClient
from issuekit.api.factory import client_for, require_api_url
from issuekit.commands._common import add_json_flag, print_json
from issuekit.config import load_config, resolve_repository_root
from issuekit.core import issue_dict
from issuekit.errors import WorkflowError
from issuekit.guards.author import (
    STOP_SENTINEL,
    guards_dict,
    read_author_guards,
    stop_message,
)
from issuekit.issues.display import dependency_detail_lines, dependency_marker
from issuekit.prompts.protocol import effective_agent_roles
from issuekit.proposals.model import ProposalError
from issuekit.store import ApiStore
from issuekit.urls import api_url_origin
from issuekit.workflow import resolve_implementer


@dataclass
class ApiStatus:
    active_issues: list
    completed_count: int
    latest_completed_id: int | None
    incoming_proposals: list
    pending_proposal_checks: int
    api_error: str | None
    author_guards: list


def register(subparsers: argparse._SubParsersAction) -> None:
    info_parser = subparsers.add_parser("info", help="Show issue tracker status.")
    add_json_flag(info_parser)
    info_parser.set_defaults(func=run)


def run(args) -> int:
    repo_root = resolve_repository_root(Path.cwd())
    config = load_config(repo_root)
    status = _collect_api_status(config)
    summary = _build_summary(config, status)
    if args.json:
        print_json(summary)
        return 1 if status.api_error else 0

    _print_human(summary, status.active_issues, status.author_guards)
    return 1 if status.api_error else 0


def _collect_api_status(config) -> ApiStatus:
    active_issues = []
    completed_count = 0
    latest_completed_id = None
    incoming_proposals = []
    pending_proposal_checks = 0
    api_error = None
    try:
        require_api_url(config, "API store")
        with client_for(config, keepalive=True) as client:
            with ApiStore(config, client=client) as store:
                active_issues = store.find_for()
                completed_count = store.count_issues(
                    status="completed", include_completed=True
                )
                latest_completed_id = store.latest_issue_id(
                    status="completed",
                    include_completed=True,
                    total=completed_count,
                )
            incoming_proposals = client.list_proposals(status="pending")
            pending_proposal_checks = _pending_proposal_check_count(config, client)
    except (ProposalError, ValueError, WorkflowError) as exc:
        api_error = str(exc)
    author_guards = read_author_guards(resolve_repository_root(Path.cwd()))

    return ApiStatus(
        active_issues=active_issues,
        completed_count=completed_count,
        latest_completed_id=latest_completed_id,
        incoming_proposals=incoming_proposals,
        pending_proposal_checks=pending_proposal_checks,
        api_error=api_error,
        author_guards=author_guards,
    )


def _build_summary(config, status: ApiStatus) -> dict:
    enabled_agents = [name for name, _run_config in config.agents]
    return {
        "counts": {
            "active": len(status.active_issues),
            "completed": status.completed_count,
            "total": len(status.active_issues) + status.completed_count,
        },
        "latestCompletedId": status.latest_completed_id,
        "pendingProposalChecks": status.pending_proposal_checks,
        "worker": config.worker_key(),
        "workerPresent": config.worker is not None,
        "enabledAgents": enabled_agents,
        "disabledAgents": list(config.disabled_agents),
        "configuredDefaultImplementer": config.default_implementer or None,
        "defaultImplementer": resolve_implementer(None, config),
        "agentRoles": effective_agent_roles(config.agent_roles, enabled_agents),
        "machineConfigPath": (
            str(config.machine_config_path) if config.machine_config_path is not None else None
        ),
        "repoConfigSource": config.repo_config_source,
        "apiUrlSource": config.api_url_source,
        "apiUrlTrustedBy": config.api_url_trusted_by,
        "apiUrlOrigin": api_url_origin(config.api_url),
        "apiError": status.api_error,
        "agentConfigs": {
            name: {
                "binary": run_config.binary,
                "model": run_config.model,
                "reasoningEffort": run_config.reasoning_effort,
                "approvalFlag": run_config.approval_flag,
                "approvalValue": run_config.approval_value,
                "headlessArgv": list(run_config.headless_argv),
                "modelPromptKeys": [model for model, _prompt in run_config.model_prompts],
                "roleOverlays": {
                    role: {
                        "model": overlay.model or run_config.model,
                        "reasoningEffort": overlay.reasoning_effort
                        or run_config.reasoning_effort,
                    }
                    for role, overlay in dict(config.agent_role_overlays).get(name, ())
                },
            }
            for name, run_config in config.agents
        },
        "activeIssues": [
            issue_dict(issue)
            | {
                "priority": issue.priority or None,
                "stage": issue.stage or None,
            }
            for issue in status.active_issues
        ],
        "incomingProposals": [
            {
                "id": proposal.get("id"),
                "origin": proposal.get("origin", ""),
                "title": proposal.get("title", ""),
                "created": proposal.get("created"),
            }
            for proposal in status.incoming_proposals
        ],
        "authorGuards": guards_dict(status.author_guards),
    }


def _print_human(summary: dict, active_issues: list, author_guards: list) -> None:
    api_error = summary["apiError"]
    print("Issue tracker status")
    print(f"- Active issues: {summary['counts']['active']}")
    print(f"- Completed issues: {summary['counts']['completed']}")
    print(f"- Total issues: {summary['counts']['total']}")
    print(f"- Latest completed id: {summary['latestCompletedId']}")
    print(f"- Incoming proposals: {len(summary['incomingProposals'])}")
    print(f"- Pending proposal checks: {summary['pendingProposalChecks']}")
    print(f"- Worker: {summary['worker'] or '-'}")
    print(f"- Machine config: {summary['machineConfigPath'] or '-'}")
    print(f"- Repository config: {summary['repoConfigSource']}")
    print(
        f"- API URL: {summary['apiUrlOrigin'] or '-'} "
        f"(from {summary['apiUrlSource']}; trusted by {summary['apiUrlTrustedBy']})"
    )
    if api_error:
        print(f"- API error: {api_error}")
    print(f"- Default implementer: {summary['defaultImplementer'] or '-'}")
    print(
        "- Configured default implementer: "
        f"{summary['configuredDefaultImplementer'] or '-'}"
    )
    for guard in author_guards:
        if guard.kind == "issue":
            print(f"- Author guard: {STOP_SENTINEL} {guard.kind} {guard.ref or guard.id}")
        else:
            print(f"- Author guard: {stop_message(guard)}")

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
        for role, overlay in agent_config["roleOverlays"].items():
            print(
                f"  {role}: model={overlay['model'] or '-'} "
                f"reasoning_effort={overlay['reasoningEffort'] or '-'}"
            )

    print()
    print("Agent roles")
    for name, role in summary["agentRoles"].items():
        print(f"- {name}: {role}")

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


def _pending_proposal_check_count(config, client: IssuekitClient) -> int:
    worker_keys = config.worker_lookup_keys()
    if not config.api_url or not worker_keys:
        return 0
    checks = {
        int(check["id"])
        for worker_key in worker_keys
        for check in client.list_proposal_checks(
            target_worker=worker_key,
            status="pending",
        )
    }
    return len(checks)
