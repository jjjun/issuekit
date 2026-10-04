import pytest

from issuekit.config import IssuekitConfig
from issuekit.negotiation import (
    ApiNegotiationStore,
    MockNegotiationStore,
    ThreadStatus,
    Verdict,
)
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


def test_cancel_reports_when_api_rejects_cancelled_status() -> None:
    class RejectingClient(FakeIssuekitClient):
        def patch_thread(self, thread_id: int, *, status: str | None = None, **kwargs):
            self.calls.append(
                {
                    "method": "patch_thread",
                    "number": thread_id,
                    "body": {"status": status},
                }
            )
            raise WorkflowError("Unprocessable Entity", code="http_422")

    client = RejectingClient()
    store = ApiNegotiationStore(
        IssuekitConfig(api_url="https://mine.example", project="provider"),
        client=client,
    )

    with pytest.raises(
        WorkflowError,
        match="API project does not accept the cancelled status yet",
    ) as exc_info:
        store.cancel_thread("7")

    assert exc_info.value.code == "unsupported_feature"
    assert client.calls == [
        {
            "method": "patch_thread",
            "number": 7,
            "body": {"status": "cancelled"},
        }
    ]
