import pytest

from issuekit.config import IssuekitConfig
from issuekit.negotiation import (
    ApiNegotiationStore,
    MockNegotiationStore,
    NegotiationEntry,
    NegotiationIssueRefs,
    ThreadStatus,
    Verdict,
    entry_from_api,
    get_negotiation_store,
)
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


def test_mock_store_create_thread_allocates_id_and_first_entry(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")

    entry = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial contract",
        body="Use the public endpoint.",
        origin="frontend#1",
        contract="GET /items",
    )

    assert entry.thread_id == "1"
    assert entry.id == 1
    assert entry.side == "frontend"
    assert entry.verdict is Verdict.propose
    assert entry.contract == "GET /items"
    assert entry.created
    assert store.get_status(entry.thread_id) is ThreadStatus.negotiating
    assert store.get_thread(entry.thread_id) == [entry]


def test_mock_store_append_entry_preserves_order(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start here.",
        origin="frontend#1",
    )

    second = store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.counter,
        title="Counter",
        body="Use pagination.",
        origin="backend#1",
    )
    third = store.append_entry(
        first.thread_id,
        side="frontend",
        verdict=Verdict.agree,
        title="Agreed",
        body="Pagination accepted.",
        origin="frontend#2",
    )

    assert [entry.id for entry in store.get_thread(first.thread_id)] == [
        first.id,
        second.id,
        third.id,
    ]
    assert [entry.verdict for entry in store.get_thread(first.thread_id)] == [
        Verdict.propose,
        Verdict.counter,
        Verdict.agree,
    ]


def test_mock_store_status_round_trips(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")
    entry = store.create_thread(
        side="frontend",
        verdict="propose",
        title="Initial",
        body="Start.",
        origin="frontend#1",
    )

    store.set_status(entry.thread_id, ThreadStatus.agreed)

    assert store.get_status(entry.thread_id) is ThreadStatus.agreed
    assert store.get_agreed_contract(entry.thread_id) is None


def test_mock_store_json_persistence_round_trips(tmp_path) -> None:
    path = tmp_path / "negotiations.json"
    first_store = MockNegotiationStore(path)
    first = first_store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="frontend#1",
    )
    second = first_store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.blocked,
        title="Blocked",
        body="Need API support.",
        origin="backend#1",
    )
    first_store.set_status(first.thread_id, ThreadStatus.blocked)

    second_store = MockNegotiationStore(path)

    assert second_store.get_status(first.thread_id) is ThreadStatus.blocked
    assert second_store.get_thread(first.thread_id) == [first, second]
    next_entry = second_store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Next",
        body="Another thread.",
        origin="frontend#2",
    )
    assert next_entry.thread_id == "2"
    assert next_entry.id == 3


def test_mock_store_lists_thread_summaries(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="frontend#1",
        contract="GET /items",
    )
    second = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Other",
        body="Start.",
        origin="frontend#2",
    )
    store.set_status(second.thread_id, ThreadStatus.blocked)

    summaries = store.list_threads(status=ThreadStatus.negotiating)

    assert [summary.thread_id for summary in summaries] == [first.thread_id]
    assert summaries[0].status is ThreadStatus.negotiating
    assert summaries[0].agreed_contract is None


def test_mock_store_freezes_agreed_contract_and_persists_it(tmp_path) -> None:
    path = tmp_path / "negotiations.json"
    first_store = MockNegotiationStore(path)
    first = first_store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="frontend#1",
        contract="GET /items",
    )
    first_store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="Agreed",
        body="Accepted.",
        origin="backend#1",
        contract="GET /items",
    )
    first_store.set_status(first.thread_id, ThreadStatus.agreed)

    second_store = MockNegotiationStore(path)

    assert second_store.get_status(first.thread_id) is ThreadStatus.agreed
    assert second_store.get_agreed_contract(first.thread_id) == "GET /items"


def test_mock_store_issue_refs_round_trip(tmp_path) -> None:
    path = tmp_path / "negotiations.json"
    first_store = MockNegotiationStore(path)
    first = first_store.create_thread(
        side="frontend",
        verdict=Verdict.agree,
        title="Agreed",
        body="Accepted.",
        origin="frontend#1",
        contract="GET /items",
    )
    first_store.set_status(first.thread_id, ThreadStatus.agreed)
    refs = NegotiationIssueRefs(
        backend_issue_ref="backend#4",
        frontend_issue_ref="frontend#9",
    )
    first_store.set_issue_refs(first.thread_id, refs)

    second_store = MockNegotiationStore(path)

    assert second_store.get_issue_refs(first.thread_id) == refs


def test_mock_store_rejects_contract_over_cap(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")

    with pytest.raises(WorkflowError) as excinfo:
        store.create_thread(
            side="frontend",
            verdict=Verdict.propose,
            title="Initial",
            body="Start.",
            origin="frontend#1",
            contract="x" * 100001,
        )

    assert excinfo.value.code == "invalid_value"


def test_mock_store_rejects_changes_after_terminal_status(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="frontend#1",
    )
    store.set_status(first.thread_id, ThreadStatus.blocked)

    with pytest.raises(WorkflowError) as append_exc:
        store.append_entry(
            first.thread_id,
            side="backend",
            verdict=Verdict.counter,
            title="Counter",
            body="No.",
            origin="backend#1",
        )
    with pytest.raises(WorkflowError) as status_exc:
        store.set_status(first.thread_id, ThreadStatus.agreed)

    assert append_exc.value.code == "invalid_transition"
    assert status_exc.value.code == "invalid_transition"


def test_negotiation_entry_rejects_invalid_side_token() -> None:
    with pytest.raises(ValueError, match="Invalid side token"):
        NegotiationEntry(
            thread_id="1",
            side="front end",
            verdict=Verdict.propose,
            contract=None,
            title="Initial",
            body="Start.",
            origin="frontend#1",
            created="2026-01-01",
        )


def test_negotiation_entry_rejects_invalid_verdict() -> None:
    with pytest.raises(ValueError, match="Invalid verdict"):
        NegotiationEntry(
            thread_id="1",
            side="frontend",
            verdict="maybe",
            contract=None,
            title="Initial",
            body="Start.",
            origin="frontend#1",
            created="2026-01-01",
        )


def test_entry_from_api_selects_created_timestamp_fallbacks() -> None:
    raw = {
        "id": 1,
        "thread_id": 7,
        "side": "frontend",
        "verdict": "propose",
        "title": "Initial",
        "body": "Start.",
        "origin": "frontend#1",
    }

    created_at_entry = entry_from_api(
        {**raw, "created_at": "2026-07-02T10:00:00Z", "created": "2026-07-02"}
    )
    created_entry = entry_from_api(
        {**raw, "created_at": None, "created": "2026-07-02"}
    )

    assert created_at_entry.created == "2026-07-02T10:00:00Z"
    assert created_entry.created == "2026-07-02"
    assert entry_from_api(raw).created == ""


def test_mock_store_rejects_invalid_verdict(tmp_path) -> None:
    store = MockNegotiationStore(tmp_path / "negotiations.json")

    with pytest.raises(ValueError, match="Invalid verdict"):
        store.create_thread(
            side="frontend",
            verdict="maybe",
            title="Initial",
            body="Start.",
            origin="frontend#1",
        )


def test_negotiation_store_context_managers_close_only_owned_clients(
    tmp_path,
    monkeypatch,
) -> None:
    class CloseTrackingClient(FakeIssuekitClient):
        def __init__(self) -> None:
            super().__init__()
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    owned_client = CloseTrackingClient()
    monkeypatch.setattr(
        "issuekit.negotiation.api_store.IssuekitClient",
        lambda *args, **kwargs: owned_client,
    )
    with ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target")
    ):
        pass

    injected_client = CloseTrackingClient()
    with ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=injected_client,
    ):
        pass

    with MockNegotiationStore(tmp_path / "negotiations.json"):
        pass

    assert owned_client.close_count == 1
    assert injected_client.close_count == 0


def test_api_negotiation_store_round_trips_via_fake_client() -> None:
    client = FakeIssuekitClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=client,
    )

    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="frontend#1",
        contract="GET /items",
    )
    second = store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.counter,
        title="Counter",
        body="Add pagination.",
        origin="backend#1",
        contract="GET /items?page=1",
    )
    third = store.append_entry(
        first.thread_id,
        side="frontend",
        verdict=Verdict.agree,
        title="Agreed",
        body="Pagination accepted.",
        origin="frontend#2",
        contract="GET /items?page=1",
    )
    store.set_status(first.thread_id, ThreadStatus.agreed, agreed_contract="GET /items?page=1")

    assert first.thread_id == "1"
    assert [entry.id for entry in store.get_thread(first.thread_id)] == [first.id, second.id, third.id]
    assert [summary.thread_id for summary in store.list_threads(status=ThreadStatus.agreed)] == [
        first.thread_id
    ]
    assert store.get_status(first.thread_id) is ThreadStatus.agreed
    assert store.get_agreed_contract(first.thread_id) == "GET /items?page=1"
    refs = NegotiationIssueRefs(
        backend_issue_ref="target#4",
        frontend_issue_ref="source#8",
    )
    store.set_issue_refs(first.thread_id, refs)
    assert store.get_issue_refs(first.thread_id) == refs

    with pytest.raises(WorkflowError) as append_exc:
        store.append_entry(
            first.thread_id,
            side="backend",
            verdict=Verdict.counter,
            title="Too late",
            body="No.",
            origin="backend#2",
        )
    with pytest.raises(WorkflowError) as status_exc:
        store.set_status(first.thread_id, ThreadStatus.blocked)

    assert append_exc.value.code == "invalid_transition"
    assert status_exc.value.code == "invalid_transition"


def test_api_store_create_thread_treats_same_payload_as_idempotent_retry() -> None:
    client = FakeIssuekitClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=client,
    )
    entry_kwargs = dict(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="source#1@abc:round-1",
        contract="GET /items",
    )

    first = store.create_thread(**entry_kwargs)
    retry = store.create_thread(**entry_kwargs)

    assert retry.id == first.id
    assert retry.thread_id == first.thread_id


def test_api_store_create_thread_rejects_same_origin_with_different_payload() -> None:
    client = FakeIssuekitClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=client,
    )
    store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Start.",
        origin="source#1@abc:round-1",
        contract="GET /items",
    )

    with pytest.raises(WorkflowError) as excinfo:
        store.create_thread(
            side="frontend",
            verdict=Verdict.propose,
            title="Different opener",
            body="Other body.",
            origin="source#1@abc:round-1",
            contract="GET /items",
        )

    assert excinfo.value.code == "duplicate_origin"
    message = str(excinfo.value)
    assert "source#1@abc:round-1" in message
    assert "title" in message and "body" in message


def test_api_store_errors_include_negotiation_context() -> None:
    class FailingClient(FakeIssuekitClient):
        def create_proposal(self, **kwargs):
            raise WorkflowError("Internal Server Error", code="http_500")

        def reply_proposal(self, proposal_id, **kwargs):
            raise WorkflowError("Internal Server Error", code="http_500")

    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=FailingClient(),
    )

    with pytest.raises(WorkflowError) as excinfo:
        store.create_thread(
            side="frontend",
            verdict=Verdict.propose,
            title="Initial",
            body="Start.",
            origin="source#1@abc:round-1",
        )

    assert excinfo.value.code == "http_500"
    message = str(excinfo.value)
    assert "negotiation origin source#1@abc:round-1" in message
    assert "target project target" in message


def test_api_store_issue_refs_fail_clearly_when_thread_fields_are_missing() -> None:
    class OldThreadClient(FakeIssuekitClient):
        def get_thread(self, thread_id: int):
            payload = super().get_thread(thread_id)
            payload.pop("backend_issue_ref", None)
            payload.pop("frontend_issue_ref", None)
            return payload

    client = OldThreadClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=client,
    )
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Use the public endpoint.",
        origin="source#1",
        contract="GET /items",
    )

    with pytest.raises(WorkflowError) as excinfo:
        store.get_issue_refs(first.thread_id)

    assert excinfo.value.code == "server_schema_drift"
    assert "issue-ref fields" in str(excinfo.value)


def test_api_store_accepts_empty_nested_issue_refs_as_supported() -> None:
    class NestedIssueRefsClient(FakeIssuekitClient):
        def get_thread(self, thread_id: int):
            payload = super().get_thread(thread_id)
            payload.pop("backend_issue_ref", None)
            payload.pop("frontend_issue_ref", None)
            payload["issue_refs"] = {}
            return payload

    client = NestedIssueRefsClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=client,
    )
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Use the public endpoint.",
        origin="source#1",
        contract="GET /items",
    )

    assert store.get_issue_refs(first.thread_id) is None


def test_api_store_set_issue_refs_requires_patch_confirmation() -> None:
    class IgnoredIssueRefsClient(FakeIssuekitClient):
        def patch_thread(self, thread_id: int, **kwargs):
            payload = super().patch_thread(thread_id, **kwargs)
            payload["backend_issue_ref"] = None
            payload["frontend_issue_ref"] = None
            return payload

    client = IgnoredIssueRefsClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="target"),
        client=client,
    )
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="Initial",
        body="Use the public endpoint.",
        origin="source#1",
        contract="GET /items",
    )
    refs = NegotiationIssueRefs(
        backend_issue_ref="target#4",
        frontend_issue_ref="source#8",
    )

    with pytest.raises(WorkflowError) as excinfo:
        store.set_issue_refs(first.thread_id, refs)

    assert excinfo.value.code == "server_schema_drift"
    assert "did not confirm" in str(excinfo.value)


def test_get_negotiation_store_selects_mock_or_api(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    mock_store = get_negotiation_store(IssuekitConfig(), use_mock=True)
    api_store = get_negotiation_store(
        IssuekitConfig(api_url="https://mine.example"),
        use_mock=False,
    )

    assert isinstance(mock_store, MockNegotiationStore)
    assert isinstance(api_store, ApiNegotiationStore)


def test_get_negotiation_store_requires_api_url_for_api_backend() -> None:
    with pytest.raises(WorkflowError) as excinfo:
        get_negotiation_store(IssuekitConfig(), use_mock=False)

    assert excinfo.value.code == "missing_api_url"
