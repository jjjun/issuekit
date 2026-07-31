"""Negotiation thread data model and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from issuekit.core import is_valid_workflow_token
from issuekit.prompts import canonical_contract_token
from issuekit.workflow import WorkflowError

DEFAULT_NEGOTIATION_PATH = Path(".agent-runs") / "negotiations" / "mock.json"
MAX_CONTRACT_LENGTH = 100000


class Verdict(StrEnum):
    propose = "propose"
    counter = "counter"
    agree = "agree"
    blocked = "blocked"


class ThreadStatus(StrEnum):
    negotiating = "negotiating"
    agreed = "agreed"
    blocked = "blocked"
    cancelled = "cancelled"


@dataclass(frozen=True)
class ProposalNegotiationSource:
    proposal_id: int
    proposal_ref: str
    thread_id: str
    title: str
    body: str
    origin: str
    target_project: str
    initiator_side: str


@dataclass(frozen=True)
class NegotiationEntry:
    thread_id: str
    side: str
    verdict: Verdict
    contract: str | None
    title: str
    body: str
    origin: str
    created: str
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.thread_id:
            raise ValueError("thread_id is required")
        if not self.side or not is_valid_workflow_token(self.side):
            raise ValueError(f"Invalid side token: {self.side}")
        object.__setattr__(self, "verdict", coerce_verdict(self.verdict))


@dataclass(frozen=True)
class NegotiationIssueRefs:
    backend_issue_ref: str
    frontend_issue_ref: str

    def __post_init__(self) -> None:
        if not self.backend_issue_ref.strip():
            raise ValueError("backend_issue_ref is required")
        if not self.frontend_issue_ref.strip():
            raise ValueError("frontend_issue_ref is required")

    def to_dict(self) -> dict[str, str]:
        return {
            "backend_issue_ref": self.backend_issue_ref,
            "frontend_issue_ref": self.frontend_issue_ref,
        }


@dataclass(frozen=True)
class NegotiationThreadSummary:
    thread_id: str
    status: ThreadStatus
    agreed_contract: str | None = None
    issue_refs: NegotiationIssueRefs | None = None
    source_proposal_ref: str | None = None
    updated: str = ""


class NegotiationStore(Protocol):
    def __enter__(self) -> NegotiationStore:
        """Enter a negotiation store lifecycle context."""

    def __exit__(self, *_: object) -> None:
        """Exit a negotiation store lifecycle context."""

    def close(self) -> None:
        """Release any store-owned resources."""

    def create_thread(
        self,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        """Create a negotiation thread and return its first entry."""

    def begin_proposal_thread(
        self,
        proposal_id: int,
        *,
        initiator_project: str,
        initiator_side: str,
    ) -> ProposalNegotiationSource:
        """Lock a pending proposal and create or reuse its negotiation thread."""

    def append_initial_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        """Append the first agent entry to a proposal-seeded thread."""

    def append_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        """Append one entry to an existing negotiation thread."""

    def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
        """Return a thread's entries in stable ascending order."""

    def list_threads(self, *, status: ThreadStatus | None = None) -> list[NegotiationThreadSummary]:
        """Return known negotiation threads in stable ascending order."""

    def set_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        *,
        agreed_contract: str | None = None,
    ) -> None:
        """Set the status for a negotiation thread."""

    def get_status(self, thread_id: str) -> ThreadStatus:
        """Return the status for a negotiation thread."""

    def get_agreed_contract(self, thread_id: str) -> str | None:
        """Return the agreed contract frozen on the thread, if any."""

    def get_issue_refs(self, thread_id: str) -> NegotiationIssueRefs | None:
        """Return implementation issue refs recorded on a finalized thread."""

    def get_source_proposal_ref(self, thread_id: str) -> str | None:
        """Return the pending proposal that seeded this thread, if any."""

    def set_issue_refs(self, thread_id: str, refs: NegotiationIssueRefs) -> None:
        """Record implementation issue refs for a finalized thread."""

    def settle_thread_members(self, thread_id: str) -> None:
        """Discard pending proposal rows that represent negotiation turns."""

    def cancel_thread(self, thread_id: str) -> None:
        """Cancel a proposal negotiation without discarding its source proposal."""

    def finalize_proposal_thread(
        self,
        thread_id: str,
        *,
        consumer_project: str,
        author: str,
        priority: str,
        provider_title: str,
        provider_body: str,
        consumer_title: str,
        consumer_body: str,
    ) -> NegotiationIssueRefs:
        """Atomically finalize a proposal thread into provider and consumer issues."""


def coerce_verdict(value: object) -> Verdict:
    if isinstance(value, Verdict):
        return value
    canonical = canonical_contract_token(value, (verdict.value for verdict in Verdict))
    try:
        return Verdict(canonical)
    except ValueError as exc:
        raise ValueError(f"Invalid verdict: {value}") from exc


def validate_entry_input(side: str, verdict: object) -> None:
    if not side or not is_valid_workflow_token(side):
        raise ValueError(f"Invalid side token: {side}")
    coerce_verdict(verdict)


def validate_contract(contract: str | None) -> None:
    if contract is not None and len(contract) > MAX_CONTRACT_LENGTH:
        raise WorkflowError(
            f"Negotiation contract exceeds {MAX_CONTRACT_LENGTH} characters.",
            code="invalid_value",
        )


def coerce_status(value: object) -> ThreadStatus:
    try:
        return value if isinstance(value, ThreadStatus) else ThreadStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid thread status: {value}") from exc


def latest_agree_contract(entries: list[NegotiationEntry]) -> str | None:
    for entry in reversed(entries):
        if entry.verdict is Verdict.agree and entry.contract is not None:
            return entry.contract
    return None
