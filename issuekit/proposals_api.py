"""Public helpers for API-backed cross-repository proposals."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import subprocess

from issuekit.client import IssuekitClient
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.proposals import Proposal, ProposalError, origin_destination
from issuekit.store import get_store


def adopt_outcome(proposal_id: str | int, project: str, issue: dict) -> dict:
    raw_issue_id = issue.get("id")
    try:
        issue_id = int(raw_issue_id)
    except (TypeError, ValueError):
        issue_id = None
    created_api_issue = issue_id is not None and issue_id > 0
    issue_ref = f"{project}#{issue_id}" if created_api_issue else None
    next_command = (
        f"issuekit claim --id {issue_id} --assignee <agent>"
        if created_api_issue
        else None
    )
    instruction = (
        f"Use issue #{issue_id} next."
        if created_api_issue
        else (
            "Adoption did not return a created API issue. Run `issuekit author` "
            "from the adopted proposal content to create an active API issue."
        )
    )
    outcome = dict(issue)
    outcome.update(
        {
            "api_result": "created_issue" if created_api_issue else "no_issue_created",
            "created_api_issue": created_api_issue,
            "proposal_id": str(proposal_id),
            "issue_id": issue_id if created_api_issue else None,
            "issue_ref": issue_ref,
            "next_command": next_command,
            "instruction": instruction,
            "issue": issue,
        }
    )
    return outcome


def api_client(config: IssuekitConfig, *, project: str | None = None) -> IssuekitClient:
    if not config.api_url:
        raise ProposalError(
            "Proposal commands require api_url in issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL."
        )
    return IssuekitClient(
        config.api_url,
        project=project or config.project,
        timeout=config.api_timeout,
    )


def proposal_id_arg(value: str) -> int:
    try:
        proposal_id = int(value)
    except ValueError as exc:
        raise ProposalError(f"Proposal id must be an integer in API mode: {value}") from exc
    if proposal_id <= 0:
        raise ProposalError(f"Proposal id must be positive: {value}")
    return proposal_id


def build_proposal(
    cwd: Path,
    *,
    to: str | None,
    title: str | None,
    body: str | None,
    body_file: str | None,
    from_issue: str | None,
    reply: str | None,
) -> Proposal:
    config = load_config(cwd)
    if not config.api_url:
        raise ProposalError(
            "Proposal commands require api_url in issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL."
        )

    source_issue: Issue | None = None
    reply_to = ""
    if reply is not None:
        source_issue = _get_issue(cwd, config, reply)
        reply_to = source_issue.frontmatter.data.get("origin", "").strip()
        if not reply_to:
            raise ProposalError(f"Issue #{source_issue.id} has no origin field.")
        to = to or origin_destination(reply_to)
    elif from_issue is not None:
        source_issue = _get_issue(cwd, config, from_issue)

    if not to:
        raise ProposalError("--to is required unless --reply is used.")

    title = title or (source_issue.title if source_issue is not None else "")
    if not title:
        raise ProposalError("--title is required unless --from-issue or --reply provides one.")

    proposal_body = _proposal_body(body, body_file, source_issue)
    origin_id = str(source_issue.id) if source_issue is not None and source_issue.id is not None else "0"
    origin_project = config.project
    origin = f"{origin_project}#{origin_id}@{_git_commit(cwd)}"
    return Proposal(
        origin=origin,
        to=to,
        reply_to=reply_to,
        created=date.today().isoformat(),
        title=title,
        body=proposal_body,
    )


def _get_issue(cwd: Path, config: IssuekitConfig, raw_id: str) -> Issue:
    issue_id = parse_issue_id_arg(raw_id)
    issue = get_store(config, config.issues_path(cwd)).get_issue(issue_id)
    if issue is None:
        raise LookupError(f"Issue #{issue_id} was not found.")
    return issue


def _proposal_body(body: str | None, body_file: str | None, source_issue: Issue | None) -> str:
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    if source_issue is not None:
        return source_issue.frontmatter.body.strip()
    return "## Context\n\n## Suggested Change\n\n## Rationale"


def _git_commit(cwd: Path) -> str:
    try:
        # stdin must be redirected: when this runs inside the issuekit-mcp stdio
        # server, an inherited stdin pipe makes `git` block until the timeout.
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"
