"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from issuekit.core import issue_dict
from issuekit.issues.service import approve_issue
from issuekit.mcp.runtime import MCP_SESSION, McpRuntime
from issuekit.workflow import claim_next, require_implementer
from issuekit.workflow import next_review as workflow_next_review
from issuekit.workflow import request_changes as workflow_request_changes
from issuekit.workflow import submit_for_review as workflow_submit_for_review


def register(server: FastMCP, rt: McpRuntime) -> None:
    async def claim_next_task_impl(
        assignee: str | None = None,
        priority: str | None = None,
        allow_author_session: bool = False,
        allow_any_branch: bool = False,
        no_sync: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, config_root, store):
            resolved_assignee = require_implementer(
                assignee,
                config,
                flag="assignee",
            )
            issue = claim_next(
                resolved_assignee,
                priority=priority,
                config=config,
                store=store,
                cwd=config_root,
                allow_author_guard_override=allow_author_session,
                allow_any_branch=allow_any_branch,
                no_sync=no_sync,
                session=MCP_SESSION,
            )
        if issue is None:
            return {"status": "none", "assignee": resolved_assignee}
        return issue_dict(issue, include_body=True)

    claim_description = (
        "Implementer protocol step 1: claim the next task, then implement and call "
        "submit_for_review."
    )

    if rt.allow_overrides:
        @server.tool(description=claim_description)
        async def claim_next_task(
            assignee: str | None = None,
            priority: str | None = None,
            allow_author_session: bool = False,
            allow_any_branch: bool = False,
            no_sync: bool = False,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await claim_next_task_impl(
                assignee=assignee,
                priority=priority,
                allow_author_session=allow_author_session,
                allow_any_branch=allow_any_branch,
                no_sync=no_sync,
                ctx=ctx,
            )
    else:
        @server.tool(description=claim_description)
        async def claim_next_task(
            assignee: str | None = None,
            priority: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await claim_next_task_impl(
                assignee=assignee,
                priority=priority,
                ctx=ctx,
            )

    async def submit_for_review_impl(
        id: int,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
        allow_author_session: bool = False,
        allow_any_branch: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, config_root, store):
            issue = workflow_submit_for_review(
                id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
                config=config,
                store=store,
                cwd=config_root,
                allow_author_guard_override=allow_author_session,
                allow_any_branch=allow_any_branch,
                session=MCP_SESSION,
            )
        return issue_dict(issue)

    submit_description = (
        "Implementation protocol step 2: submit an implemented task for reviewer "
        "handoff with summary and optional branch/commit metadata."
    )

    if rt.allow_overrides:
        @server.tool(description=submit_description)
        async def submit_for_review(
            id: int,
            summary: str,
            branch: str | None = None,
            commit: str | None = None,
            reviewer: str | None = None,
            allow_author_session: bool = False,
            allow_any_branch: bool = False,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await submit_for_review_impl(
                id=id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
                allow_author_session=allow_author_session,
                allow_any_branch=allow_any_branch,
                ctx=ctx,
            )
    else:
        @server.tool(description=submit_description)
        async def submit_for_review(
            id: int,
            summary: str,
            branch: str | None = None,
            commit: str | None = None,
            reviewer: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await submit_for_review_impl(
                id=id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
                ctx=ctx,
            )

    @server.tool(
        description=(
            "Reviewer protocol step 1: fetch the next issue waiting for the reviewer, "
            "then call approve or request_changes."
        )
    )
    async def next_review(
        reviewer: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, _config_root, store):
            issue = workflow_next_review(reviewer, config=config, store=store)
        if issue is None:
            return {
                "status": "none",
                "assignee": reviewer or None,
                "stage": "review",
            }
        return issue_dict(issue, include_body=True)

    @server.tool(
        description="Reviewer protocol decision: return a review issue to its implementer with notes."
    )
    async def request_changes(
        id: int,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, _config_root, store):
            issue = workflow_request_changes(
                id,
                notes=notes,
                reviewer=reviewer,
                assignee=assignee,
                config=config,
                store=store,
                session=MCP_SESSION,
            )
        return issue_dict(issue)

    @server.tool(
        description="Reviewer protocol decision: approve a reviewed issue and move it to completed."
    )
    async def approve(
        id: int,
        verification: str,
        reviewer: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, _config_root, store):
            issue = approve_issue(
                id,
                verification=verification,
                reviewer=reviewer,
                config=config,
                store=store,
                session=MCP_SESSION,
            )
        return issue_dict(issue)
