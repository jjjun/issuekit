"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from issuekit.commands.approve import approve_issue
from issuekit.commands.edit import edit_issue
from issuekit.config import load_config
from issuekit.core import issue_dict
from issuekit.protocol import render_protocol, render_server_instructions
from issuekit.proposals_api import (
    adopt_outcome,
    api_client,
    build_proposal,
    list_outgoing_proposals,
    payload_mismatch_guidance,
    proposal_id_arg,
    proposal_payload_mismatch,
)
from issuekit.store import ApiStore, get_store
from issuekit.workflow import (
    AUTO_REVIEWER,
    WorkflowError,
    claim_next,
    find_for,
    request_changes as workflow_request_changes,
    resolve_reviewer,
    submit_for_review as workflow_submit_for_review,
)


def create_server(cwd: Path | str | None = None) -> FastMCP:
    server = FastMCP("issuekit", instructions=render_server_instructions())
    root = Path.cwd() if cwd is None else Path(cwd)

    @server.tool(description="Read the current issuekit handoff protocol.")
    def get_protocol(agent: str | None = None, role: str | None = None) -> str:
        return render_protocol(agent, role=role)

    @server.tool(
        description=(
            "Codex protocol step 1: claim the next task, then implement and call "
            "submit_for_review."
        )
    )
    def claim_next_task(assignee: str = "codex", priority: str | None = None) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = claim_next(assignee, priority=priority, config=config, store=store)
        if issue is None:
            return {"status": "none", "assignee": assignee}
        return issue_dict(issue, include_body=True)

    @server.tool(
        description=(
            "Implementation protocol step 2: submit an implemented task for reviewer "
            "handoff with summary and optional branch/commit metadata."
        )
    )
    def submit_for_review(
        id: int,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        assignee: str = "codex",
        reviewer: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = workflow_submit_for_review(
                id,
                summary=summary,
                branch=branch,
                commit=commit,
                assignee=assignee,
                reviewer=reviewer,
                config=config,
                store=store,
            )
        return issue_dict(issue)

    @server.tool(
        description=(
            "Reviewer protocol step 1: fetch the next issue waiting for the reviewer, "
            "then call approve or request_changes."
        )
    )
    def next_review(reviewer: str | None = None) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            if reviewer is None and config.default_reviewer == AUTO_REVIEWER:
                issues = find_for(stage="review", config=config, store=store)
                if not issues:
                    return {"status": "none", "assignee": AUTO_REVIEWER, "stage": "review"}
                return issue_dict(issues[0], include_body=True)
            reviewer = resolve_reviewer(reviewer, config)
            issues = find_for(reviewer, stage="review", config=config, store=store)
        if not issues:
            return {"status": "none", "assignee": reviewer, "stage": "review"}
        return issue_dict(issues[0], include_body=True)

    @server.tool(
        description="Reviewer protocol decision: return a review issue to codex with notes."
    )
    def request_changes(
        id: int,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = workflow_request_changes(
                id,
                notes=notes,
                reviewer=reviewer,
                assignee=assignee,
                config=config,
                store=store,
            )
        return issue_dict(issue)

    @server.tool(
        description="Reviewer protocol decision: approve a reviewed issue and move it to completed."
    )
    def approve(id: int, verification: str, reviewer: str | None = None) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = approve_issue(
                id,
                verification=verification,
                reviewer=reviewer,
                config=config,
                store=store,
            )
        return issue_dict(issue)

    @server.tool(description="Read one active or completed issue by id.")
    def get_issue(id: int) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = store.get_issue(id)
        if issue is None:
            return {"status": "none", "id": id}
        return issue_dict(issue, include_body=True)

    @server.tool(description="Edit an API-backed issue title, body, appended text, or priority.")
    def update_issue(
        id: int,
        title: str | None = None,
        body: str | None = None,
        append: str | None = None,
        priority: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if body is not None and append is not None:
            raise ValueError("body and append are mutually exclusive.")
        config = load_config(root)
        with get_store(config) as store:
            issue = edit_issue(
                id,
                title=title,
                body=body,
                append=append,
                priority=priority,
                force=force,
                config=config,
                store=store,
            )
        return issue_dict(issue, include_body=True)

    @server.tool(description="List active queue entries, optionally filtered by assignee and stage.")
    def list_queue(assignee: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        config = load_config(root)
        with get_store(config) as store:
            return [
                issue_dict(issue)
                for issue in find_for(assignee, stage=stage, config=config, store=store)
            ]

    @server.tool(description="Send a cross-repository proposal to a configured ref.")
    def propose(
        to: str | None = None,
        title: str | None = None,
        body: str | None = None,
        from_issue: str | None = None,
        reply: str | None = None,
    ) -> dict[str, Any]:
        proposal = build_proposal(
            root,
            to=to,
            title=title,
            body=body,
            body_file=None,
            from_issue=from_issue,
            reply=reply,
        )
        config = load_config(root)
        with api_client(config, project=proposal.to) as client:
            created = client.create_proposal(
                origin=proposal.origin,
                title=proposal.title,
                body=proposal.body,
                reply_to=proposal.reply_to or None,
            )
        result = dict(created)
        mismatched = proposal_payload_mismatch(proposal, created)
        result["payload_mismatch"] = bool(mismatched)
        if mismatched:
            result["idempotent_existing"] = True
            result["payload_mismatch_fields"] = mismatched
            result["warning"] = payload_mismatch_guidance(proposal, created, mismatched)
        return result

    @server.tool(description="List incoming cross-repository proposals.")
    def list_incoming() -> list[dict[str, Any]]:
        config = load_config(root)
        with api_client(config) as client:
            return client.list_proposals(status="pending")

    @server.tool(
        description=(
            "List proposals this project sent to a target project's inbox "
            "(read-only, scoped to proposals this project authored)."
        )
    )
    def list_outgoing(to: str, status: str | None = None) -> list[dict[str, Any]]:
        config = load_config(root)
        return list_outgoing_proposals(config, to=to, status=status)

    @server.tool(description="Adopt an incoming proposal as a local active issue.")
    def adopt_proposal(
        proposal_id: int | None = None,
        proposal_file: str | None = None,
        priority: str = "medium",
        append: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(root)
        raw_id = proposal_id if proposal_id is not None else proposal_id_arg(proposal_file or "")
        with api_client(config) as client:
            issue = client.adopt_proposal(int(raw_id), priority=priority)
            if append is not None:
                issue_id = _adopted_issue_id(issue)
                if issue_id is None:
                    raise WorkflowError(
                        "Adoption did not return a created API issue; cannot append to the issue body."
                    )
                edit_issue(
                    issue_id,
                    append=append,
                    config=config,
                    store=ApiStore(config, client=client),
                )
                issue = client.get_issue(issue_id)
        return adopt_outcome(raw_id, config.project, issue)

    @server.tool(description="Discard an incoming cross-repository proposal.")
    def discard_proposal(
        proposal_id: int | None = None,
        proposal_file: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(root)
        raw_id = proposal_id if proposal_id is not None else proposal_id_arg(proposal_file or "")
        with api_client(config) as client:
            return client.discard_proposal(int(raw_id))

    return server


def main() -> None:
    asyncio.run(create_server().run_stdio_async())


def _adopted_issue_id(issue: dict[str, Any]) -> int | None:
    try:
        issue_id = int(issue.get("id"))
    except (TypeError, ValueError):
        return None
    return issue_id if issue_id > 0 else None
