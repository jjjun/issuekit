"""Public helpers for API-backed cross-repository proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from issuekit.client import IssuekitClient
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.gitutil import git_short_head
from issuekit.proposals import Proposal, ProposalError, origin_destination
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


OUTGOING_PROPOSAL_STATUSES = ("pending", "adopted", "discarded")


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


class ProposalAppendError(ProposalError):
    """Raised after adoption succeeds but appending extra issue content fails."""

    def __init__(self, message: str, *, outcome: dict, append_error: str) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.append_error = append_error


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


def send_proposal(config: IssuekitConfig, proposal: Proposal) -> dict:
    """Create a proposal and annotate idempotent payload conflicts."""
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


def adopt_proposal_with_append(
    config: IssuekitConfig,
    proposal_id: str | int,
    *,
    priority: str | None,
    append_text: str | None = None,
    append_file: str | None = None,
) -> dict:
    if append_text is not None and append_file is not None:
        raise ValueError("append_text and append_file are mutually exclusive.")
    raw_id = proposal_id_arg(str(proposal_id))
    with api_client(config) as client:
        issue = client.adopt_proposal(raw_id, priority=priority)
        if append_text is not None or append_file is not None:
            issue_id = _adopted_issue_id(issue)
            outcome = adopt_outcome(proposal_id, config.project, issue)
            if issue_id is None:
                message = (
                    "Adopted proposal, but no API issue id was returned; cannot append file."
                    if append_file is not None
                    else "Adoption did not return a created API issue; cannot append to the issue body."
                )
                raise ProposalAppendError(message, outcome=outcome, append_error=message)
            try:
                from issuekit.commands.edit import edit_issue
                from issuekit.store import ApiStore

                edit_issue(
                    issue_id,
                    append=append_text,
                    append_file=append_file,
                    config=config,
                    store=ApiStore(config, client=client),
                )
                issue = client.get_issue(issue_id)
            except (OSError, UnicodeError, ValueError, WorkflowError) as exc:
                raise ProposalAppendError(
                    f"Adopted proposal as issue #{issue_id}, but append failed: {exc}",
                    outcome=outcome,
                    append_error=str(exc),
                ) from exc
    return adopt_outcome(proposal_id, config.project, issue)


def proposal_payload_mismatch(proposal: Proposal, created: Mapping[str, Any]) -> list[str]:
    """Fields where an idempotent same-origin response differs from the request."""
    if created.get("origin") != proposal.origin:
        return []
    mismatched = []
    if _proposal_text(created.get("title")) != _proposal_text(proposal.title):
        mismatched.append("title")
    if _proposal_text(created.get("body")) != _proposal_text(proposal.body):
        mismatched.append("body")
    if (created.get("reply_to") or None) != (proposal.reply_to or None):
        mismatched.append("reply_to")
    return mismatched


def payload_mismatch_guidance(
    proposal: Proposal,
    created: Mapping[str, Any],
    mismatched: Sequence[str],
) -> str:
    return (
        f"Proposal was not sent: {proposal.to} already has pending proposal "
        f"#{created.get('id')} with origin {proposal.origin} but different "
        f"{', '.join(mismatched)}. Use --from-issue <id> to derive a distinct "
        f"origin, or adopt/discard the stale pending proposal in {proposal.to}. "
        "Avoid reusing the implicit #0 origin for unrelated proposals from one commit."
    )


def list_outgoing_proposals(
    config: IssuekitConfig,
    *,
    to: str,
    status: str | None = None,
) -> list[dict]:
    """List proposals this project sent to another project's inbox (read-only)."""
    if status is not None and status not in OUTGOING_PROPOSAL_STATUSES:
        raise ProposalError(
            f"Invalid proposal status: {status}. "
            f"Expected one of {', '.join(OUTGOING_PROPOSAL_STATUSES)}."
        )
    statuses = (status,) if status else OUTGOING_PROPOSAL_STATUSES
    outgoing = []
    with api_client(config, project=to) as client:
        for candidate_status in statuses:
            outgoing.extend(
                proposal
                for proposal in client.list_proposals(status=candidate_status)
                if _is_own_origin(proposal.get("origin"), config.project)
            )
    outgoing.sort(key=lambda proposal: int(proposal.get("id", 0)))
    return outgoing


def get_outgoing_proposal(config: IssuekitConfig, *, to: str, proposal_id: int) -> dict:
    """Read one proposal this project sent to another project's inbox."""
    with api_client(config, project=to) as client:
        proposal = client.get_proposal(int(proposal_id))
    if not _is_own_origin(proposal.get("origin"), config.project):
        raise ProposalError(
            f"Proposal #{proposal_id} in {to} was not sent by {config.project}."
        )
    return proposal


def _is_own_origin(origin: object, project: str) -> bool:
    return isinstance(origin, str) and origin.startswith(f"{project}#")


def _proposal_text(value: object) -> str:
    return str(value or "").strip()


def _adopted_issue_id(issue: dict) -> int | None:
    try:
        issue_id = int(issue.get("id"))
    except (TypeError, ValueError):
        return None
    return issue_id if issue_id > 0 else None


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
        source_issue = _get_issue(config, reply)
        reply_to = source_issue.metadata.get("origin", "").strip()
        if not reply_to:
            raise ProposalError(f"Issue #{source_issue.id} has no origin field.")
        to = to or origin_destination(reply_to)
    elif from_issue is not None:
        source_issue = _get_issue(config, from_issue)

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


def _get_issue(config: IssuekitConfig, raw_id: str) -> Issue:
    issue_id = parse_issue_id_arg(raw_id)
    with get_store(config) as store:
        issue = store.get_issue(issue_id)
    if issue is None:
        raise LookupError(f"Issue #{issue_id} was not found.")
    return issue


def _proposal_body(body: str | None, body_file: str | None, source_issue: Issue | None) -> str:
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    if source_issue is not None:
        return source_issue.body.strip()
    return "## Context\n\n## Suggested Change\n\n## Rationale"


def _git_commit(cwd: Path) -> str:
    return git_short_head(cwd) or "unknown"
