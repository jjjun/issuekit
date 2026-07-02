"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from issuekit.author_guard import STOP_SENTINEL, create_author_guard, guard_dict
from issuekit.commands.approve import approve_issue
from issuekit.commands.edit import edit_issue
from issuekit.config import load_config
from issuekit.core import issue_dict
from issuekit.protocol import render_protocol, render_server_instructions
from issuekit.proposals_api import (
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    list_outgoing_proposals,
    proposal_id_arg,
    send_proposal,
)
from issuekit.store import get_store
from issuekit.worker_registry import list_api_workers
from issuekit.workflow import (
    claim_next,
    find_for,
    next_review as workflow_next_review,
    request_changes as workflow_request_changes,
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
    def claim_next_task(
        assignee: str = "codex",
        priority: str | None = None,
        allow_author_session: bool = False,
    ) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = claim_next(
                assignee,
                priority=priority,
                config=config,
                store=store,
                cwd=root,
                allow_author_guard_override=allow_author_session,
            )
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
        reviewer: str | None = None,
        allow_author_session: bool = False,
    ) -> dict[str, Any]:
        config = load_config(root)
        with get_store(config) as store:
            issue = workflow_submit_for_review(
                id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
                config=config,
                store=store,
                cwd=root,
                allow_author_guard_override=allow_author_session,
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
            issue = workflow_next_review(reviewer, config=config, store=store)
        if issue is None:
            return {
                "status": "none",
                "assignee": reviewer or config.default_reviewer,
                "stage": "review",
            }
        return issue_dict(issue, include_body=True)

    @server.tool(
        description="Reviewer protocol decision: return a review issue to its implementer with notes."
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

    @server.tool(
        description=(
            "List registered workers and their repo-level roles across issuekit "
            "checkouts, optionally filtered by repo_id and project."
        )
    )
    def list_workers(
        repo_id: str | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        config = load_config(root)
        return list_api_workers(config, repo_id=repo_id, project=project)

    @server.tool(
        description=(
            "Send a cross-repository proposal from the origin project to the target "
            "project inbox; use this instead of authoring directly in the target repo."
        )
    )
    def propose(
        to: str | None = None,
        title: str | None = None,
        body: str | None = None,
        from_issue: str | None = None,
        reply: str | None = None,
        blocking: bool = False,
        agent: str | None = None,
    ) -> dict[str, Any]:
        proposal = build_proposal(
            root,
            to=to,
            title=title,
            body=body,
            body_file=None,
            from_issue=from_issue,
            reply=reply,
            blocking=blocking,
        )
        config = load_config(root)
        sent = send_proposal(config, proposal)
        if sent.get("payload_mismatch"):
            return sent
        guard = create_author_guard(
            root,
            config=config,
            kind="proposal",
            item_id=sent.get("id"),
            ref=f"{proposal.to}#{sent.get('id')}",
            target_project=proposal.to,
            author_agent=agent,
        )
        sent = dict(sent)
        sent["authorGuard"] = guard_dict(guard)
        sent["stop"] = STOP_SENTINEL
        return sent

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
        return adopt_proposal_with_append(
            config,
            raw_id,
            priority=priority,
            append_text=append,
        )

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
