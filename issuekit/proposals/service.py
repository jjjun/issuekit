"""Proposal operations shared by commands and integrations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from issuekit.config import IssuekitConfig
from issuekit.guards.author import AuthorGuard, create_author_guard

from .api import api_client, build_proposal, discard_outgoing_proposal, send_proposal
from .model import Proposal


@dataclass(frozen=True)
class ProposeOutcome:
    sent: dict
    proposal: Proposal
    guard: AuthorGuard | None
    mismatched: bool
    deduplicated: bool


def propose_with_guard(
    cwd: Path,
    config: IssuekitConfig,
    *,
    to: str | None,
    title: str | None,
    body: str | None,
    body_file: str | None,
    from_issue: str | None,
    reply: str | None,
    blocking: bool,
    depends_on: str | Sequence[str] | None,
    author_agent: str | None,
    session: str | None,
) -> ProposeOutcome:
    proposal = build_proposal(
        cwd,
        to=to,
        title=title,
        body=body,
        body_file=body_file,
        from_issue=from_issue,
        reply=reply,
        blocking=blocking,
        depends_on=depends_on,
        config=config,
    )
    sent = send_proposal(config, proposal)
    mismatched = bool(sent.get("payload_mismatch"))
    deduplicated = bool(sent.get("deduplicated") or sent.get("idempotent_existing"))
    guard = None
    if not mismatched and not deduplicated:
        guard = create_author_guard(
            cwd,
            config=config,
            kind="proposal",
            item_id=sent.get("id"),
            ref=f"{proposal.to}#{sent.get('id')}",
            target_project=proposal.to,
            author_agent=author_agent,
            author_session=session,
        )
    return ProposeOutcome(
        sent=sent,
        proposal=proposal,
        guard=guard,
        mismatched=mismatched,
        deduplicated=deduplicated,
    )


def discard_proposal(
    config: IssuekitConfig,
    proposal_id: int,
    *,
    to: str | None = None,
) -> dict:
    if to:
        return discard_outgoing_proposal(config, to=to, proposal_id=proposal_id)
    with api_client(config) as client:
        return client.discard_proposal(proposal_id)


def list_incoming_proposals(config: IssuekitConfig) -> list[dict]:
    with api_client(config) as client:
        return client.list_proposals(status="pending")
