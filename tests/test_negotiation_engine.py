from issuekit.negotiation.engine import _evaluate_convergence
from issuekit.negotiation.model import NegotiationEntry, Verdict


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
