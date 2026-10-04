import pytest

from issuekit.negotiation.engine import load_thread_inspection
from issuekit.negotiation.mock_store import MockNegotiationStore
from issuekit.negotiation.model import (
    NegotiationEntry,
    NegotiationIssueRefs,
    ThreadStatus,
    Verdict,
)
from issuekit.negotiation.thread import _evaluate_convergence, finalize_refusal_reason


def _entry(side: str, verdict: Verdict, contract: str | None) -> NegotiationEntry:
    return NegotiationEntry(
        thread_id="1",
        side=side,
        verdict=verdict,
        contract=contract,
        title=f"{side} {verdict.value}",
        body="Negotiation turn.",
        origin=f"frontend#108@{side}:round-1",
        created="2026-01-01",
    )


def test_agree_without_contract_does_not_conclude_negotiation() -> None:
    thread = [
        NegotiationEntry(
            thread_id="1",
            side="provider",
            verdict=Verdict.propose,
            contract="GET /items",
            title="Proposed contract",
            body="Expose the item endpoint.",
            origin="provider#1",
            created="2026-01-01",
        ),
        NegotiationEntry(
            thread_id="1",
            side="consumer",
            verdict=Verdict.agree,
            contract=None,
            title="Agree without contract",
            body="Looks good.",
            origin="consumer#1",
            created="2026-01-02",
        ),
    ]

    assert _evaluate_convergence(thread) == "negotiating"


def test_load_thread_inspection_reads_thread_state_from_store(tmp_path) -> None:
    class TrackingStore(MockNegotiationStore):
        def __init__(self) -> None:
            self.reads: list[str] = []
            super().__init__(tmp_path / "negotiations.json")

        def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
            self.reads.append("thread")
            return super().get_thread(thread_id)

        def get_status(self, thread_id: str) -> ThreadStatus:
            self.reads.append("status")
            return super().get_status(thread_id)

        def get_issue_refs(self, thread_id: str) -> NegotiationIssueRefs | None:
            self.reads.append("issue_refs")
            return super().get_issue_refs(thread_id)

        def get_agreed_contract(self, thread_id: str) -> str | None:
            self.reads.append("agreed_contract")
            return super().get_agreed_contract(thread_id)

    store = TrackingStore()
    first = store.create_thread(
        side="provider",
        verdict=Verdict.propose,
        title="provider propose",
        body="Start.",
        origin="frontend#108@provider:round-1",
        contract="GET /items",
    )
    store.append_entry(
        first.thread_id,
        side="consumer",
        verdict=Verdict.agree,
        title="consumer agree",
        body="Accepted.",
        origin="frontend#108@consumer:round-2",
        contract="GET /items",
    )
    store.set_status(first.thread_id, ThreadStatus.agreed, agreed_contract="GET /items")

    inspection = load_thread_inspection(store, first.thread_id)

    assert store.reads == ["thread", "status", "issue_refs", "agreed_contract"]
    assert inspection.status is ThreadStatus.agreed
    assert inspection.agreed_contract == "GET /items"
    assert inspection.outcome == "agreed"


@pytest.mark.parametrize(
    ("thread", "expected_outcome", "expected_refusal"),
    [
        ([], "negotiating", "thread has no entries"),
        (
            [_entry("provider", Verdict.blocked, "GET /items")],
            "blocked",
            "at least one turn is blocked",
        ),
        (
            [
                _entry("provider", Verdict.propose, "GET /items"),
                _entry("consumer", Verdict.agree, None),
            ],
            "negotiating",
            "latest agree turn has no contract",
        ),
        (
            [_entry("consumer", Verdict.agree, "GET /items")],
            "negotiating",
            "no counterpart turn exists",
        ),
        (
            [
                _entry("provider", Verdict.propose, "GET /items"),
                _entry("consumer", Verdict.agree, "POST /items"),
            ],
            "negotiating",
            "latest agree contract does not match a counterpart contract",
        ),
        (
            [
                _entry("provider", Verdict.propose, "GET /items 200"),
                _entry("consumer", Verdict.agree, "GET /items   200"),
            ],
            "agreed",
            None,
        ),
        (
            [
                _entry("provider", Verdict.agree, "GET /items 200"),
                _entry("consumer", Verdict.agree, "GET /items 200"),
                _entry("provider", Verdict.counter, "POST /items 201"),
            ],
            "agreed",
            "latest verdict is counter, not agree",
        ),
    ],
    ids=[
        "empty-thread",
        "blocked-turn",
        "agree-without-contract",
        "no-counterpart",
        "contract-mismatch",
        "matching-contract",
        "earlier-matching-agreements",
    ],
)
def test_convergence_and_finalize_refusal(thread, expected_outcome, expected_refusal) -> None:
    assert _evaluate_convergence(thread) == expected_outcome
    assert finalize_refusal_reason(ThreadStatus.negotiating, thread) == expected_refusal
