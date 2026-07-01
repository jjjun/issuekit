import pytest

from issuekit.config import IssuekitConfig
from issuekit.negotiation import (
    ApiNegotiationStore,
    MockNegotiationStore,
    NegotiationEntry,
    ThreadStatus,
    Verdict,
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
    assert store.get_status(first.thread_id) is ThreadStatus.agreed
    assert store.get_agreed_contract(first.thread_id) == "GET /items?page=1"

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
