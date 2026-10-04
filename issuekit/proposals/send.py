"""Send proposals and coordinate shared proposal command flows."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from issuekit.config import IssuekitConfig
from issuekit.guards.author import AuthorGuard, create_author_guard
from issuekit.issues.dependencies import bare_ref_collision_warnings

from .build import _dependency_tuple, _proposal_text, build_proposal
from .client import api_client, validate_target_project
from .model import Proposal
from .outgoing import discard_outgoing_proposal


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


def send_proposal(config: IssuekitConfig, proposal: Proposal) -> dict:
    """Create a proposal and annotate idempotent payload conflicts."""
    validate_target_project(config, proposal.to)
    with api_client(config, project=proposal.to) as client:
        created = client.create_proposal(
            origin=proposal.origin,
            title=proposal.title,
            body=proposal.body,
            reply_to=proposal.reply_to or None,
            blocking=True if proposal.blocking else None,
            depends_on=list(proposal.depends_on) or None,
            target_worker=proposal.target_worker or None,
        )
    result = dict(created)
    deduplicated = result.pop("was_created", None) is False
    result["deduplicated"] = deduplicated
    dependency_ref = proposal_dependency_ref(proposal.to, result.get("id"))
    if dependency_ref is not None:
        result["dependency_ref"] = dependency_ref
    if proposal.depends_on and "depends_on" not in result:
        result["depends_on"] = list(proposal.depends_on)
    warnings = [
        *proposal.warnings,
        *bare_ref_collision_warnings(_dependency_rows_from_response(result)),
    ]
    if warnings:
        result["warnings"] = list(dict.fromkeys(warnings))
    if deduplicated:
        result["idempotent_existing"] = True
    mismatched = proposal_payload_mismatch(proposal, created)
    result["payload_mismatch"] = bool(mismatched)
    if mismatched:
        result["idempotent_existing"] = True
        result["payload_mismatch_fields"] = mismatched
        result["warning"] = payload_mismatch_guidance(proposal, created, mismatched)
    return result


def proposal_dependency_ref(project: str, proposal_id: object) -> str | None:
    try:
        raw_id = int(str(proposal_id).strip())
    except (TypeError, ValueError):
        return None
    if raw_id <= 0:
        return None
    return f"{project}#proposal:{raw_id}"


def proposal_payload_mismatch(proposal: Proposal, created: Mapping[str, Any]) -> list[str]:
    """Fields where a deduplicated response differs from the request."""
    mismatched = []
    if _proposal_text(created.get("title")) != _proposal_text(proposal.title):
        mismatched.append("title")
    if _proposal_text(created.get("body")) != _proposal_text(proposal.body):
        mismatched.append("body")
    if (created.get("reply_to") or None) != (proposal.reply_to or None):
        mismatched.append("reply_to")
    if bool(created.get("blocking", False)) != proposal.blocking:
        mismatched.append("blocking")
    if "depends_on" in created and _dependency_tuple(created.get("depends_on")) != proposal.depends_on:
        mismatched.append("depends_on")
    if _proposal_text(created.get("target_worker")) != proposal.target_worker:
        mismatched.append("target_worker")
    return mismatched


def _proposal_origin_issue_id(origin: str) -> int | None:
    match = re.fullmatch(r"[^#]+#(?P<issue_id>[0-9]+)@[^@]+", origin)
    return int(match.group("issue_id")) if match is not None else None


def payload_mismatch_guidance(
    proposal: Proposal,
    created: Mapping[str, Any],
    mismatched: Sequence[str],
) -> str:
    source_issue_id = _proposal_origin_issue_id(proposal.origin)
    if source_issue_id not in (None, 0):
        proposal_id = created.get("id")
        return (
            f"Proposal was not sent: source issue #{source_issue_id} already has pending proposal "
            f"#{proposal_id} in {proposal.to} from this commit with different {', '.join(mismatched)}. "
            "One source issue can have only "
            "one pending proposal per target project per commit. Send a separate proposal "
            "without --from-issue (implicit #0 origin; dropping --reply also drops the reply link), "
            "or adopt/discard "
            f"pending proposal #{proposal_id} first."
        )
    return (
        f"Proposal was not sent: {proposal.to} already has pending proposal "
        f"#{created.get('id')} with origin {proposal.origin} but different "
        f"{', '.join(mismatched)}. Use --from-issue <id> to derive a distinct "
        f"origin, or adopt/discard the stale pending proposal in {proposal.to}. "
        "Avoid reusing the implicit #0 origin for unrelated proposals from one commit."
    )


def _dependency_rows_from_response(raw: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    _extend_dependency_rows(rows, raw)
    issue = raw.get("issue")
    if isinstance(issue, Mapping):
        _extend_dependency_rows(rows, issue)
    proposal = raw.get("proposal")
    if isinstance(proposal, Mapping):
        _extend_dependency_rows(rows, proposal)
    return tuple(rows)


def _extend_dependency_rows(rows: list[dict[str, object]], raw: Mapping[str, Any]) -> None:
    for key in ("dependencies", "dependency_resolutions", "resolved_dependencies"):
        value = raw.get(key)
        if not isinstance(value, list):
            continue
        rows.extend(dict(item) for item in value if isinstance(item, Mapping))
