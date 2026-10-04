"""Read and discard proposals sent to other projects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from issuekit.api import IssuekitClient
from issuekit.config import IssuekitConfig
from issuekit.timestamps import parse_timestamp

from .client import _is_own_origin, _target_repo, api_client, validate_target_project
from .model import ProposalError

OUTGOING_PROPOSAL_STATUSES = ("pending", "adopted", "discarded")


def list_outgoing_proposals(
    config: IssuekitConfig,
    *,
    to: str,
    status: str | None = None,
) -> list[dict]:
    """List proposals this project sent to another project's inbox (read-only)."""
    to = _target_repo(to, label="--to")
    with api_client(config, project=to) as client:
        validate_target_project(config, to, client=client)
        if status is not None and status not in OUTGOING_PROPOSAL_STATUSES:
            raise ProposalError(
                f"Invalid proposal status: {status}. "
                f"Expected one of {', '.join(OUTGOING_PROPOSAL_STATUSES)}."
            )
        statuses = (status,) if status else OUTGOING_PROPOSAL_STATUSES
        outgoing = client.list_proposals_board(
            projects=[to],
            statuses=statuses,
            origin_project=config.project,
        )
        outgoing = [
            proposal
            for proposal in outgoing
            if _is_own_origin(proposal.get("origin"), config.project)
        ]
        outgoing.sort(key=lambda proposal: int(proposal.get("id", 0)))
        return [_with_proposal_check_waiting_times(proposal) for proposal in outgoing]


def list_outgoing_proposal_rows(
    client: IssuekitClient,
    *,
    project: str,
    statuses: Sequence[str],
) -> list[dict]:
    """List raw proposals sent by a project without fetching enrichment data."""
    outgoing = [
        proposal
        for status in statuses
        for proposal in client.list_proposals(status=status)
        if _is_own_origin(proposal.get("origin"), project)
    ]
    outgoing.sort(key=lambda proposal: int(proposal.get("id", 0)))
    return outgoing


def get_outgoing_proposal(config: IssuekitConfig, *, to: str, proposal_id: int) -> dict:
    """Read one proposal this project sent to another project's inbox."""
    to = _target_repo(to, label="--to")
    with api_client(config, project=to) as client:
        validate_target_project(config, to, client=client)
        proposal = client.get_proposal(int(proposal_id))
        if not _is_own_origin(proposal.get("origin"), config.project):
            raise ProposalError(
                f"Proposal #{proposal_id} in {to} was not sent by {config.project}."
            )
        return _with_proposal_check_waiting_times(proposal)


def discard_outgoing_proposal(config: IssuekitConfig, *, to: str, proposal_id: int) -> dict:
    """Discard one pending proposal this project sent to another project's inbox."""
    to = _target_repo(to, label="--to")
    validate_target_project(config, to)
    with api_client(config, project=to) as client:
        proposal = client.get_proposal(int(proposal_id))
        if not _is_own_origin(proposal.get("origin"), config.project):
            raise ProposalError(
                f"Proposal #{proposal_id} in {to} was not sent by {config.project}; "
                "refusing to discard it."
            )
        return client.discard_proposal(int(proposal_id))


def _with_proposal_check_waiting_times(proposal: Mapping[str, Any]) -> dict:
    enriched = dict(proposal)
    enriched["proposal_checks"] = [
        _with_proposal_check_waiting_time(check)
        for check in proposal.get("proposal_checks", [])
    ]
    return enriched


def _with_proposal_check_waiting_time(check: Mapping[str, Any]) -> dict:
    enriched = dict(check)
    if check.get("status") != "pending":
        return enriched
    created_at = parse_timestamp(check.get("created_at"))
    if created_at is not None:
        enriched["waiting_seconds"] = max(
            0,
            int((datetime.now(UTC) - created_at).total_seconds()),
        )
    return enriched
