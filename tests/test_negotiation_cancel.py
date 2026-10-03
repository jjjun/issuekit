import pytest

from issuekit import cli
from issuekit.config import IssuekitConfig
from issuekit.negotiation import ApiNegotiationStore, MockNegotiationStore, ThreadStatus, Verdict
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


def test_mock_store_rejects_cancelling_blocked_thread(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")
    entry = store.create_thread(
        side="consumer",
        verdict=Verdict.propose,
        title="Initial contract",
        body="Use the pending proposal.",
        origin="consumer#1",
    )
    store.set_status(entry.thread_id, ThreadStatus.blocked)

    with pytest.raises(WorkflowError) as exc_info:
        store.cancel_thread(entry.thread_id)
    assert exc_info.value.code == "invalid_transition"
    assert "already blocked" in str(exc_info.value)

    assert store.get_status(entry.thread_id) is ThreadStatus.blocked


def test_fake_api_rejects_cancelling_blocked_proposal_thread() -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 7,
                "target_project": "provider",
                "origin": "consumer#12@abc123",
                "title": "Add the shared endpoint",
                "body": "The provider should expose GET /items.",
            }
        ]
    )
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="provider"),
        client=client,
    )
    source = store.begin_proposal_thread(
        7,
        initiator_project="consumer",
        initiator_side="consumer",
    )
    store.set_status(source.thread_id, ThreadStatus.blocked)

    with pytest.raises(WorkflowError) as cancel_exc:
        store.cancel_thread(source.thread_id)
    assert cancel_exc.value.code == "invalid_transition"
    assert "already blocked" in str(cancel_exc.value)

    assert store.get_status(source.thread_id) is ThreadStatus.blocked
    with pytest.raises(WorkflowError) as adopt_exc:
        client.adopt_proposal(7)
    assert adopt_exc.value.code == "proposal_negotiating"
    assert "locked by negotiation" in str(adopt_exc.value)


class _ProposalRefMockStore(MockNegotiationStore):
    def get_source_proposal_ref(self, thread_id: str) -> str | None:
        self._ensure_thread(thread_id)
        return "provider#proposal:7"


def test_cancel_blocked_proposal_reports_missing_server_transition(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.commands import negotiate

    store = _ProposalRefMockStore(None)
    entry = store.create_thread(
        side="consumer",
        verdict=Verdict.propose,
        title="Initial contract",
        body="Use the pending proposal.",
        origin="consumer#1",
    )
    store.set_status(entry.thread_id, ThreadStatus.blocked)
    (tmp_path / "issuekit.toml").write_text(
        "project = 'consumer'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(negotiate, "get_negotiation_store", lambda *args, **kwargs: store)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        ["negotiate", "--cancel", entry.thread_id, "--to", "provider"]
    )

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "provider#proposal:7" in error
    assert "mine-py API does not support" in error
    assert "blocked proposal negotiation to cancelled" in error
    assert store.get_status(entry.thread_id) is ThreadStatus.blocked
