"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from issuekit.commands.approve import approve_issue
from issuekit.commands.propose import _api_client, _proposal_id_arg, _use_api, build_proposal
from issuekit.config import load_config
from issuekit.core import (
    Issue,
    issue_dict,
    read_active_issues,
)
from issuekit.proposals import (
    adopt_proposal as adopt_proposal_file,
    discard_proposal as discard_proposal_file,
    list_incoming as list_incoming_files,
    proposal_dict,
    write_proposal,
)
from issuekit.refs import resolve_ref
from issuekit.protocol import render_protocol, render_server_instructions
from issuekit.store import get_store
from issuekit.workflow import (
    AUTO_REVIEWER,
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
        config, issues_dir = _context(root)
        issue = claim_next(issues_dir, assignee, priority=priority, config=config)
        if issue is None:
            return {"status": "none", "assignee": assignee}
        return _issue_dict(issue, include_body=True)

    @server.tool(
        description=(
            "Implementation protocol step 2: submit an implemented task for reviewer "
            "handoff with summary, branch, and commit."
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
        config, issues_dir = _context(root)
        issue = workflow_submit_for_review(
            issues_dir,
            id,
            summary=summary,
            branch=branch,
            commit=commit,
            assignee=assignee,
            reviewer=reviewer,
            config=config,
        )
        return _issue_dict(issue)

    @server.tool(
        description=(
            "Reviewer protocol step 1: fetch the next issue waiting for the reviewer, "
            "then call approve or request_changes."
        )
    )
    def next_review(reviewer: str | None = None) -> dict[str, Any]:
        config, issues_dir = _context(root)
        if reviewer is None and config.default_reviewer == AUTO_REVIEWER:
            issues = find_for(issues_dir, stage="review", config=config)
            if not issues:
                return {"status": "none", "assignee": AUTO_REVIEWER, "stage": "review"}
            return _issue_dict(issues[0], include_body=True)
        reviewer = resolve_reviewer(reviewer, config)
        issues = find_for(issues_dir, reviewer, stage="review", config=config)
        if not issues:
            return {"status": "none", "assignee": reviewer, "stage": "review"}
        return _issue_dict(issues[0], include_body=True)

    @server.tool(
        description="Reviewer protocol decision: return a review issue to codex with notes."
    )
    def request_changes(
        id: int,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
    ) -> dict[str, Any]:
        config, issues_dir = _context(root)
        issue = workflow_request_changes(
            issues_dir,
            id,
            notes=notes,
            reviewer=reviewer,
            assignee=assignee,
            config=config,
        )
        return _issue_dict(issue)

    @server.tool(
        description="Reviewer protocol decision: approve a reviewed issue and move it to completed."
    )
    def approve(id: int, verification: str, reviewer: str | None = None) -> dict[str, Any]:
        config, issues_dir = _context(root)
        issue = approve_issue(
            issues_dir,
            id,
            verification=verification,
            reviewer=reviewer,
            config=config,
        )
        return _issue_dict(issue)

    @server.tool(description="Read one active or completed issue by id.")
    def get_issue(id: int) -> dict[str, Any]:
        config, issues_dir = _context(root)
        issue = get_store(config, issues_dir).get_issue(id)
        if issue is None:
            return {"status": "none", "id": id}
        return _issue_dict(issue, include_body=True)

    @server.tool(description="List active queue entries, optionally filtered by assignee and stage.")
    def list_queue(assignee: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        config, issues_dir = _context(root)
        return [
            _issue_dict(issue)
            for issue in find_for(issues_dir, assignee, stage=stage, config=config)
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
        config, _ = _context(root)
        if _use_api(config):
            return _api_client(config, project=proposal.to).create_proposal(
                origin=proposal.origin,
                title=proposal.title,
                body=proposal.body,
                reply_to=proposal.reply_to or None,
            )
        target = resolve_ref(proposal.to, root)
        path = write_proposal(target.issues_dir, proposal)
        return {**proposal_dict(proposal), "path": path.as_posix()}

    @server.tool(description="List incoming cross-repository proposals.")
    def list_incoming() -> list[dict[str, Any]]:
        config, issues_dir = _context(root)
        if _use_api(config):
            return _api_client(config).list_proposals(status="pending")
        return [proposal_dict(proposal) for proposal in list_incoming_files(issues_dir)]

    @server.tool(description="Adopt an incoming proposal as a local active issue.")
    def adopt_proposal(
        proposal_id: int | None = None,
        proposal_file: str | None = None,
        priority: str = "medium",
    ) -> dict[str, Any]:
        config, issues_dir = _context(root)
        if _use_api(config):
            raw_id = proposal_id if proposal_id is not None else _proposal_id_arg(proposal_file or "")
            return _api_client(config).adopt_proposal(int(raw_id), priority=priority)
        raw_file = proposal_file if proposal_file is not None else str(proposal_id or "")
        path = adopt_proposal_file(issues_dir, raw_file, priority=priority)
        issues = read_active_issues(issues_dir)
        issue = next(candidate for candidate in issues if candidate.file_path == path)
        return _issue_dict(issue, include_body=True)

    @server.tool(description="Discard an incoming cross-repository proposal.")
    def discard_proposal(
        proposal_id: int | None = None,
        proposal_file: str | None = None,
    ) -> dict[str, Any]:
        config, issues_dir = _context(root)
        if _use_api(config):
            raw_id = proposal_id if proposal_id is not None else _proposal_id_arg(proposal_file or "")
            return _api_client(config).discard_proposal(int(raw_id))
        raw_file = proposal_file if proposal_file is not None else str(proposal_id or "")
        path = discard_proposal_file(issues_dir, raw_file)
        return {"path": path.as_posix()}

    return server


def main() -> None:
    asyncio.run(create_server().run_stdio_async())


def _context(root: Path):
    config = load_config(root)
    return config, config.issues_path(root)


def _issue_dict(issue: Issue, *, include_body: bool = False) -> dict[str, Any]:
    return issue_dict(issue, include_body=include_body)
