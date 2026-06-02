"""MCP server exposing issuekit workflow tools over stdio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from issuekit.commands.complete import complete_issue
from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import load_config
from issuekit.core import Issue, read_all_issues
from issuekit.protocol import render_protocol
from issuekit.workflow import (
    claim_next,
    find_for,
    request_changes as workflow_request_changes,
    submit_for_review as workflow_submit_for_review,
)


def create_server(cwd: Path | str | None = None) -> FastMCP:
    server = FastMCP("issuekit", instructions=render_protocol(None))
    root = Path.cwd() if cwd is None else Path(cwd)

    @server.tool(description="Read the current issuekit handoff protocol.")
    def get_protocol(agent: str | None = None) -> str:
        return render_protocol(agent)

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
            "Codex protocol step 2: submit an implemented task for Claude review "
            "with summary, branch, and commit."
        )
    )
    def submit_for_review(
        id: int,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
    ) -> dict[str, Any]:
        config, issues_dir = _context(root)
        issue = workflow_submit_for_review(
            issues_dir,
            id,
            summary=summary,
            branch=branch,
            commit=commit,
            config=config,
        )
        _refresh_indexes(issues_dir, config.recent_count)
        return _issue_dict(issue)

    @server.tool(
        description=(
            "Claude protocol step 1: fetch the next issue waiting for review, then "
            "call approve or request_changes."
        )
    )
    def next_review() -> dict[str, Any]:
        config, issues_dir = _context(root)
        issues = find_for(issues_dir, "claude", stage="review", config=config)
        if not issues:
            return {"status": "none", "assignee": "claude", "stage": "review"}
        return _issue_dict(issues[0], include_body=True)

    @server.tool(
        description="Claude protocol decision: return a review issue to codex with notes."
    )
    def request_changes(id: int, notes: str) -> dict[str, Any]:
        config, issues_dir = _context(root)
        issue = workflow_request_changes(issues_dir, id, notes=notes, config=config)
        _refresh_indexes(issues_dir, config.recent_count)
        return _issue_dict(issue)

    @server.tool(
        description="Claude protocol decision: approve a reviewed issue and move it to completed."
    )
    def approve(id: int, verification: str) -> dict[str, Any]:
        config, issues_dir = _context(root)
        issue = complete_issue(
            issues_dir,
            id,
            summary="Approved by claude.",
            verification=verification,
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

    return server


def main() -> None:
    asyncio.run(create_server().run_stdio_async())


def _context(root: Path):
    config = load_config(root)
    return config, config.issues_path(root)


def _refresh_indexes(issues_dir: Path, recent_count: int) -> None:
    write_index_files(issues_dir, recent_count)


def _issue_dict(issue: Issue, *, include_body: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": issue.id,
        "title": issue.title,
        "status": issue.issue_status,
        "assignee": issue.assignee,
        "stage": issue.stage,
        "implementer": issue.implementer,
        "file": issue.relative_path,
    }
    if include_body:
        data["body"] = issue.frontmatter.body
    return data
