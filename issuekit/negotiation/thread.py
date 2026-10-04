"""Pure negotiation thread logic and read-only inspection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.negotiation.model import (
    NegotiationEntry,
    NegotiationIssueRefs,
    ThreadStatus,
    Verdict,
)
from issuekit.negotiation.prompts import ParsedRound

PROVIDER_SIDE = "provider"
CONSUMER_SIDE = "consumer"


class _ProjectConfig(Protocol):
    project: str


@dataclass(frozen=True)
class NegotiationThreadInspection:
    thread_id: str
    status: ThreadStatus
    outcome: str
    final_contract: str | None
    agreed_contract: str | None
    issue_refs: NegotiationIssueRefs | None
    entries: tuple[NegotiationEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "status": self.status.value,
            "outcome": self.outcome,
            "final_contract": self.final_contract,
            "agreed_contract": self.agreed_contract,
            "issue_refs": self.issue_refs.to_dict() if self.issue_refs else None,
            "entries": [
                {
                    "id": entry.id,
                    "side": entry.side,
                    "verdict": entry.verdict.value,
                    "contract": entry.contract,
                    "title": entry.title,
                    "body": entry.body,
                    "origin": entry.origin,
                    "created": entry.created,
                }
                for entry in self.entries
            ],
            "finalize_refusal": finalize_refusal_reason(self.status, list(self.entries)),
        }


def inspect_thread(
    thread_id: str,
    *,
    thread: list[NegotiationEntry],
    status: ThreadStatus,
    issue_refs: NegotiationIssueRefs | None,
    agreed_contract: str | None,
) -> NegotiationThreadInspection:
    return NegotiationThreadInspection(
        thread_id=thread_id,
        status=status,
        outcome=_evaluate_convergence(thread),
        final_contract=_latest_contract(thread),
        agreed_contract=agreed_contract,
        issue_refs=issue_refs,
        entries=tuple(thread),
    )


def _evaluate_convergence(thread: list[NegotiationEntry]) -> str:
    if any(entry.verdict is Verdict.blocked for entry in thread):
        return "blocked"
    if not thread:
        return "negotiating"

    if latest_agree_matches_counterpart(thread):
        return "agreed"

    agreements: dict[str, str] = {}
    for entry in thread:
        if entry.verdict is not Verdict.agree:
            continue
        contract_hash = _contract_hash(entry.contract)
        if contract_hash is None:
            continue
        agreements[entry.side] = contract_hash
    if len(agreements) >= 2 and len(set(agreements.values())) == 1:
        return "agreed"
    return "negotiating"


def latest_agree_matches_counterpart(thread: list[NegotiationEntry]) -> bool:
    if not thread:
        return False
    latest = thread[-1]
    latest_hash = _contract_hash(latest.contract)
    if latest.verdict is not Verdict.agree or latest_hash is None:
        return False
    return any(
        entry.side != latest.side and _contract_hash(entry.contract) == latest_hash
        for entry in thread[:-1]
    )


def _contract_hash(contract: str | None) -> str | None:
    if contract is None:
        return None
    normalized = " ".join(contract.split()).strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _latest_contract(thread: list[NegotiationEntry]) -> str | None:
    for entry in reversed(thread):
        if entry.contract:
            return entry.contract
    return None


def _next_side(thread: list[NegotiationEntry]) -> str:
    if not thread:
        raise ValueError("A negotiation thread needs an initiating entry.")
    return _other_side(thread[-1].side)


def _other_side(side: str) -> str:
    if side == PROVIDER_SIDE:
        return CONSUMER_SIDE
    if side == CONSUMER_SIDE:
        return PROVIDER_SIDE
    raise ValueError(f"Unknown negotiation side: {side}")


def _require_supported_thread(thread_id: str, thread: list[NegotiationEntry]) -> None:
    if any(entry.side not in (PROVIDER_SIDE, CONSUMER_SIDE) for entry in thread):
        raise WorkflowError(
            f"Negotiation thread {thread_id} predates provider/consumer sides; "
            "a new thread is needed.",
            code="unsupported_thread",
        )


def finalize_refusal_reason(status: ThreadStatus, thread: list[NegotiationEntry]) -> str | None:
    if status is ThreadStatus.agreed:
        return None
    if status is ThreadStatus.blocked:
        return "thread is blocked"
    if not thread:
        return "thread has no entries"
    if any(entry.verdict is Verdict.blocked for entry in thread):
        return "at least one turn is blocked"
    latest = thread[-1]
    latest_hash = _contract_hash(latest.contract)
    if latest.verdict is not Verdict.agree:
        return f"latest verdict is {latest.verdict.value}, not agree"
    if latest_hash is None:
        return "latest agree turn has no contract"
    if not any(entry.side != latest.side for entry in thread[:-1]):
        return "no counterpart turn exists"
    if latest_agree_matches_counterpart(thread):
        return None
    return "latest agree contract does not match a counterpart contract"


def _entry_title(side: str, parsed: ParsedRound) -> str:
    return f"{side} {parsed.verdict.value}"


def entry_origin(
    issue: Issue,
    *,
    config: _ProjectConfig,
    side: str,
    round_number: int,
) -> str:
    if issue.metadata.get("source_type") == "proposal":
        source_ref = issue.ref
    else:
        issue_id = issue.id if issue.id is not None else "unknown"
        source_ref = f"{config.project}#{issue_id}"
    return f"{source_ref}@{side}:round-{round_number}"


def origin_issue_ref_from_thread(thread: list[NegotiationEntry]) -> str | None:
    if not thread:
        return None
    origin = thread[0].origin.split("@", 1)[0].strip()
    return origin or None
