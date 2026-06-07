"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from issuekit.commands.complete import complete_issue
from issuekit.commands.generate_indexes import write_index_files
from issuekit.commands.propose import build_proposal
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, issue_dict, read_all_issues
from issuekit.proposals import (
    adopt_proposal as adopt_proposal_file,
    list_incoming as list_incoming_files,
    proposal_dict,
    write_proposal,
)
from issuekit.refs import resolve_ref
from issuekit.protocol import render_protocol
from issuekit.workflow import (
    AUTO_REVIEWER,
    claim_next,
    ensure_assigned_reviewer,
    ensure_not_self_review,
    find_for,
    request_changes as workflow_request_changes,
    resolve_reviewer,
    submit_for_review as workflow_submit_for_review,
)


def create_server(cwd: Path | str | None = None) -> FastMCP:
    server = FastMCP("issuekit", instructions=render_protocol(None))
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
        _refresh_indexes(issues_dir, config.recent_count)
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
        _refresh_indexes(issues_dir, config.recent_count)
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
        _refresh_indexes(issues_dir, config.recent_count)
        return _issue_dict(issue)

    @server.tool(
        description="Reviewer protocol decision: approve a reviewed issue and move it to completed."
    )
    def approve(id: int, verification: str, reviewer: str | None = None) -> dict[str, Any]:
        config, issues_dir = _context(root)
        reviewer = _resolve_reviewer_for_issue(issues_dir, id, reviewer, config)
        issue = complete_issue(
            issues_dir,
            id,
            summary=f"Approved by {reviewer}.",
            verification=verification,
            reviewer=reviewer,
            config=config,
        )
        _refresh_indexes(issues_dir, config.recent_count)
        return _issue_dict(issue)

    @server.tool(description="Read one active or completed issue by id.")
    def get_issue(id: int) -> dict[str, Any]:
        _, issues_dir = _context(root)
        _, _, issues = read_all_issues(issues_dir)
        issue = next((candidate for candidate in issues if candidate.id == id), None)
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
        target = resolve_ref(proposal.to, root)
        path = write_proposal(target.issues_dir, proposal)
        return {**proposal_dict(proposal), "path": path.as_posix()}

    @server.tool(description="List incoming cross-repository proposals.")
    def list_incoming() -> list[dict[str, Any]]:
        _, issues_dir = _context(root)
        return [proposal_dict(proposal) for proposal in list_incoming_files(issues_dir)]

    @server.tool(description="Adopt an incoming proposal as a local active issue.")
    def adopt_proposal(proposal_file: str, priority: str = "medium") -> dict[str, Any]:
        config, issues_dir = _context(root)
        path = adopt_proposal_file(issues_dir, proposal_file, priority=priority)
        _refresh_indexes(issues_dir, config.recent_count)
        _, _, issues = read_all_issues(issues_dir)
        issue = next(candidate for candidate in issues if candidate.file_path == path)
        return _issue_dict(issue, include_body=True)

    return server


def main() -> None:
    asyncio.run(create_server().run_stdio_async())


def _context(root: Path):
    config = load_config(root)
    return config, config.issues_path(root)


def _refresh_indexes(issues_dir: Path, recent_count: int) -> None:
    write_index_files(issues_dir, recent_count)


def _resolve_reviewer_for_issue(
    issues_dir: Path,
    issue_id: int,
    reviewer: str | None,
    config: IssuekitConfig,
) -> str:
    active_issues, _, _ = read_all_issues(issues_dir)
    issue = next((candidate for candidate in active_issues if candidate.id == issue_id), None)
    resolved_reviewer = resolve_reviewer(reviewer, config, issue=issue)
    if issue is not None and issue.stage == "review":
        ensure_assigned_reviewer(issue, reviewer, resolved_reviewer)
        if not issue.assignee:
            ensure_not_self_review(issue, resolved_reviewer, config)
    return resolved_reviewer


def _issue_dict(issue: Issue, *, include_body: bool = False) -> dict[str, Any]:
    return issue_dict(issue, include_body=include_body)
