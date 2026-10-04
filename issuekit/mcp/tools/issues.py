"""MCP tools for issuekit."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from issuekit.core import issue_dict
from issuekit.issues.orphans import (
    DEFAULT_STALE_AFTER_SEC,
    list_stale_claims,
    stale_claim_dict,
)
from issuekit.issues.service import dispatch_issue as issue_dispatch
from issuekit.issues.service import edit_issue
from issuekit.mcp.runtime import McpRuntime
from issuekit.workflow import find_for, validate_queue_stage
from issuekit.workflow import readdress_issue as workflow_readdress_issue
from issuekit.workflow import reclaim_issue as workflow_reclaim_issue


def register(server: FastMCP, rt: McpRuntime) -> None:
    @server.tool(description="Read one active or completed issue by id.")
    async def get_issue(id: int, ctx: Context | None = None) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, _config_root, store):
            issue = store.get_issue(id)
        if issue is None:
            return {"status": "none", "id": id}
        return issue_dict(issue, include_body=True)

    async def update_issue_impl(
        id: int,
        title: str | None = None,
        body: str | None = None,
        append: str | None = None,
        priority: str | None = None,
        depends_on: list[str] | str | None = None,
        force: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        if body is not None and append is not None:
            raise ValueError("body and append are mutually exclusive.")
        async with rt.api_store(ctx) as (config, _config_root, store):
            issue = edit_issue(
                id,
                title=title,
                body=body,
                append=append,
                priority=priority,
                depends_on=depends_on,
                force=force,
                config=config,
                store=store,
            )
        return issue_dict(issue, include_body=True)

    update_description = "Edit an API-backed issue title, body, appended text, or priority."

    if rt.allow_overrides:
        @server.tool(description=update_description)
        async def update_issue(
            id: int,
            title: str | None = None,
            body: str | None = None,
            append: str | None = None,
            priority: str | None = None,
            depends_on: list[str] | str | None = None,
            force: bool = False,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await update_issue_impl(
                id=id,
                title=title,
                body=body,
                append=append,
                priority=priority,
                depends_on=depends_on,
                force=force,
                ctx=ctx,
            )
    else:
        @server.tool(description=update_description)
        async def update_issue(
            id: int,
            title: str | None = None,
            body: str | None = None,
            append: str | None = None,
            priority: str | None = None,
            depends_on: list[str] | str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await update_issue_impl(
                id=id,
                title=title,
                body=body,
                append=append,
                priority=priority,
                depends_on=depends_on,
                ctx=ctx,
            )

    @server.tool(
        description=(
            "List active queue entries, optionally filtered by assignee and stage "
            "(planned, todo, implementing, review, or changes_requested). "
            "Completed issues have stage done; use get_issue for those."
        )
    )
    async def list_queue(
        assignee: str | None = None,
        stage: str | None = None,
        with_body: bool = False,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        async with rt.api_store(ctx) as (config, _config_root, store):
            validate_queue_stage(stage, config)
            return [
                issue_dict(issue, include_body=with_body)
                for issue in find_for(assignee, stage=stage, config=config, store=store)
            ]

    @server.tool(
        description=(
            "List implementing issues whose claiming worker is gone or has "
            "stopped heartbeating (orphaned or stale claims that the pull pool "
            "will not re-offer)."
        )
    )
    async def list_orphans(
        stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
        ctx: Context | None = None,
    ) -> list[dict[str, Any]]:
        async with rt.api_config(ctx) as (config, _config_root):
            claims = list_stale_claims(config, stale_after_sec=stale_after_sec)
        return [stale_claim_dict(claim) for claim in claims]

    async def reclaim_issue_impl(
        id: int,
        force: bool = False,
        stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
        reason: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_config(ctx) as (config, _config_root):
            result = workflow_reclaim_issue(
                id,
                force=force,
                stale_after_sec=stale_after_sec,
                reason=reason,
                config=config,
            )
        return result.to_dict()

    reclaim_description = (
        "Return an orphaned or stale implementing claim to the implement pool. "
        "By default this refuses claims not listed by list_orphans."
    )

    if rt.allow_overrides:
        reclaim_description += " Pass force only for human emergency recovery."
        @server.tool(description=reclaim_description)
        async def reclaim_issue(
            id: int,
            force: bool = False,
            stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
            reason: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await reclaim_issue_impl(
                id=id,
                force=force,
                stale_after_sec=stale_after_sec,
                reason=reason,
                ctx=ctx,
            )
    else:
        @server.tool(description=reclaim_description)
        async def reclaim_issue(
            id: int,
            stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
            reason: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await reclaim_issue_impl(
                id=id,
                stale_after_sec=stale_after_sec,
                reason=reason,
                ctx=ctx,
            )

    @server.tool(
        description=(
            "Return a directed issue from a specific worker target back to the "
            "repo pool."
        )
    )
    async def readdress_issue(
        id: int,
        reason: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_config(ctx) as (config, _config_root):
            result = workflow_readdress_issue(
                id,
                reason=reason,
                config=config,
            )
        return result.to_dict()

    async def dispatch_issue_impl(
        id: int,
        target_worker: str,
        assignee: str | None = None,
        stage: str | None = None,
        allow_unregistered_worker: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async with rt.api_store(ctx) as (config, _config_root, store):
            issue = issue_dispatch(
                id,
                target_worker=target_worker,
                assignee=assignee,
                stage=stage,
                allow_unregistered_worker=allow_unregistered_worker,
                config=config,
                store=store,
            )
        output = issue_dict(issue)
        output["target_worker"] = issue.target_worker
        return output

    dispatch_description = (
        "Direct an issue to a registered worker; use readdress_issue to return "
        "it to the repo pool."
    )

    if rt.allow_overrides:
        @server.tool(description=dispatch_description)
        async def dispatch_issue(
            id: int,
            target_worker: str,
            assignee: str | None = None,
            stage: str | None = None,
            allow_unregistered_worker: bool = False,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await dispatch_issue_impl(
                id=id,
                target_worker=target_worker,
                assignee=assignee,
                stage=stage,
                allow_unregistered_worker=allow_unregistered_worker,
                ctx=ctx,
            )
    else:
        @server.tool(description=dispatch_description)
        async def dispatch_issue(
            id: int,
            target_worker: str,
            assignee: str | None = None,
            stage: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            return await dispatch_issue_impl(
                id=id,
                target_worker=target_worker,
                assignee=assignee,
                stage=stage,
                ctx=ctx,
            )
