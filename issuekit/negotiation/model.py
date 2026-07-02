"""Negotiation thread data model and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from issuekit.core import is_valid_workflow_token
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
        object.__setattr__(self, "verdict", _coerce_verdict(self.verdict))


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
    updated: str = ""


class NegotiationStore(Protocol):
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

    def set_issue_refs(self, thread_id: str, refs: NegotiationIssueRefs) -> None:
        """Record implementation issue refs for a finalized thread."""


def _coerce_verdict(value: object) -> Verdict:
    try:
        return value if isinstance(value, Verdict) else Verdict(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid verdict: {value}") from exc


def _validate_entry_input(side: str, verdict: object) -> None:
    if not side or not is_valid_workflow_token(side):
        raise ValueError(f"Invalid side token: {side}")
    _coerce_verdict(verdict)


def _validate_contract(contract: str | None) -> None:
    if contract is not None and len(contract) > MAX_CONTRACT_LENGTH:
        raise WorkflowError(
            f"Negotiation contract exceeds {MAX_CONTRACT_LENGTH} characters.",
            code="invalid_value",
        )


def _coerce_status(value: object) -> ThreadStatus:
    try:
        return value if isinstance(value, ThreadStatus) else ThreadStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid thread status: {value}") from exc


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _latest_agree_contract(entries: list[NegotiationEntry]) -> str | None:
    for entry in reversed(entries):
        if entry.verdict is Verdict.agree and entry.contract is not None:
            return entry.contract
    return None

