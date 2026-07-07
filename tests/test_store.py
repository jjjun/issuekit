from issuekit.config import IssuekitConfig
from issuekit.core import issue_dict
from issuekit.store import ApiStore, get_store
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue


def test_get_store_requires_api_url_for_runtime_default() -> None:
    try:
        get_store(IssuekitConfig())
    except WorkflowError as exc:
        assert exc.code == "missing_api_url"
    else:
        raise AssertionError("get_store should require api_url by default")


def test_get_store_uses_api_when_api_url_is_set() -> None:
    store = get_store(IssuekitConfig(api_url="https://mine.example"))

    assert isinstance(store, ApiStore)


def test_api_store_context_manager_closes_only_owned_clients(monkeypatch) -> None:
    owned_client = FakeIssuekitClient()
    monkeypatch.setattr("issuekit.store.IssuekitClient", lambda *args, **kwargs: owned_client)

    with ApiStore(IssuekitConfig(api_url="https://mine.example")):
        pass

    injected_client = FakeIssuekitClient()
    with ApiStore(IssuekitConfig(api_url="https://mine.example"), client=injected_client):
        pass

    assert owned_client.close_count == 1
    assert injected_client.close_count == 0


def test_api_store_maps_json_to_issue_and_issue_dict() -> None:
    body = "# Issue #7: Read Path\n\nBody text.\n"
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Read Path",
                status="in_progress",
                priority="high",
                assignee="claude",
                stage="review",
                implementer="codex",
                author="kimi",
                worker="machine/demo/checkout",
                body=body,
            )
        ]
    )
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store.get_issue(7)

    assert issue is not None
    assert issue.id == 7
    assert issue.ref == "demo#7"
    assert issue.issue_status == "in_progress"
    assert issue.priority == "high"
    assert issue.assignee == "claude"
    assert issue.stage == "review"
    assert issue.implementer == "codex"
    assert issue.author == "kimi"
    assert issue.worker == "machine/demo/checkout"
    assert issue.body == body
    assert issue.metadata["status"] == "in_progress"
    assert issue_dict(issue, include_body=True) == {
        "id": 7,
        "title": "Read Path",
        "status": "in_progress",
        "assignee": "claude",
        "stage": "review",
        "implementer": "codex",
        "author": "kimi",
        "ref": "demo#7",
        "body": body,
        "worker": "machine/demo/checkout",
    }


def test_api_store_update_issue_body_sends_body_only_update() -> None:
    client = FakeIssuekitClient([api_issue(7, "Read Path", body="Old body")])
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store.update_issue_body(7, body="Updated body")

    assert issue.body == "Updated body"
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 7,
            "body": {"body": "Updated body"},
        }
    ]


def test_api_store_update_issue_sends_editable_fields() -> None:
    client = FakeIssuekitClient([api_issue(7, "Read Path", body="Old body")])
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store.update_issue(7, title="Updated title", priority="high")

    assert issue.title == "Updated title"
    assert issue.priority == "high"
    assert client.calls == [
        {
            "method": "update_issue",
            "number": 7,
            "body": {"title": "Updated title", "priority": "high"},
        }
    ]


def test_api_store_reclaim_issue_threads_audit_fields() -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Stuck",
                status="in_progress",
                stage="implementing",
                assignee="claude",
                implementer="claude",
                worker="machine/demo/dead",
            )
        ]
    )
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store.reclaim_issue(
        7,
        expected_worker="machine/demo/dead",
        actor="machine/demo/operator",
        reason="stale checkout",
    )

    assert issue.issue_status == "active"
    assert issue.stage == "todo"
    assert issue.assignee == ""
    assert issue.implementer == ""
    assert issue.worker == ""
    assert client.calls == [
        {
            "method": "reclaim",
            "number": 7,
            "body": {
                "expected_worker": "machine/demo/dead",
                "actor": "machine/demo/operator",
                "reason": "stale checkout",
            },
        }
    ]


def test_api_store_worker_field_is_optional() -> None:
    class MissingWorkerClient(FakeIssuekitClient):
        def get_issue(self, number: int) -> dict[str, object]:
            raw = super().get_issue(number)
            raw.pop("worker", None)
            return raw

    client = MissingWorkerClient([api_issue(7, "Read Path")])
    store = ApiStore(IssuekitConfig(api_url="https://mine.example"), client=client)

    issue = store.get_issue(7)

    assert issue is not None
    assert issue.worker == ""


def test_api_store_preserves_review_handoff_metadata() -> None:
    raw_issue = api_issue(7, "Review Path", status="in_progress", stage="review")
    raw_issue.update(
        {
            "implementation_worker": "worker.demo@main1",
            "summary": "Updated the host service.",
            "branch": "main",
            "commit": "abc1234",
            "verification": "systemctl status demo.service",
        }
    )
    client = FakeIssuekitClient([raw_issue])
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store.get_issue(7)

    assert issue is not None
    assert issue.metadata["implementation_worker"] == "worker.demo@main1"
    assert issue.metadata["summary"] == "Updated the host service."
    assert issue.metadata["branch"] == "main"
    assert issue.metadata["commit"] == "abc1234"
    assert issue.metadata["verification"] == "systemctl status demo.service"


def test_api_store_maps_issue_dependencies_and_warning() -> None:
    raw_issue = api_issue(
        7,
        "Dependent",
        depends_on=["mine-py#42"],
        dependency_state="waiting",
        dependencies=[
            {
                "ref": "mine-py#42",
                "state": "waiting",
                "status": "in_progress",
                "stage": "review",
            }
        ],
    )
    client = FakeIssuekitClient([raw_issue])
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store._issue_from_response(
        {
            "issue": client.get_issue(7),
            "warning": "Issue #7 has dependency_state=waiting.",
        }
    )

    assert issue.depends_on == ("mine-py#42",)
    assert issue.dependency_state == "waiting"
    assert issue.dependencies[0]["status"] == "in_progress"
    assert issue.warning == "Issue #7 has dependency_state=waiting."
    assert issue_dict(issue) == {
        "id": 7,
        "title": "Dependent",
        "status": "active",
        "assignee": "",
        "stage": "",
        "implementer": "",
        "author": "",
        "ref": "demo#7",
        "depends_on": ["mine-py#42"],
        "dependency_state": "waiting",
        "dependencies": [
            {
                "ref": "mine-py#42",
                "state": "waiting",
                "status": "in_progress",
                "stage": "review",
            }
        ],
        "warning": "Issue #7 has dependency_state=waiting.",
    }


def test_api_store_maps_wrapped_issue_warnings_list() -> None:
    raw_issue = api_issue(7, "Dependent", dependency_state="waiting")
    client = FakeIssuekitClient([raw_issue])
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store._issue_from_response(
        {
            "issue": client.get_issue(7),
            "warnings": [
                "Issue #7 has dependency_state=waiting.",
                "Check upstream dependencies before implementing.",
            ],
        }
    )

    assert issue.warning == (
        "Issue #7 has dependency_state=waiting.\n"
        "Check upstream dependencies before implementing."
    )
    assert issue.metadata["warning"] == issue.warning


def test_api_store_finds_implementing_issues_for_worker() -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Mine",
                status="in_progress",
                stage="implementing",
                worker="machine/demo/checkout",
            ),
            api_issue(
                2,
                "Other",
                status="in_progress",
                stage="implementing",
                worker="machine/demo/other",
            ),
            api_issue(
                3,
                "Done",
                status="completed",
                stage="implementing",
                worker="machine/demo/checkout",
            ),
        ]
    )
    store = ApiStore(IssuekitConfig(api_url="https://mine.example"), client=client)

    issues = store.find_implementing_for_worker("machine/demo/checkout")

    assert [issue.id for issue in issues] == [1]


def test_api_store_partitions_and_filters_active_issues() -> None:
    class RecordingClient(FakeIssuekitClient):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.list_calls: list[dict[str, object]] = []

        def list_all_issues(self, **kwargs):
            self.list_calls.append(kwargs)
            return super().list_all_issues(**kwargs)

    client = RecordingClient(
        [
            api_issue(1, "Review", status="in_progress", assignee="claude", stage="review"),
            api_issue(2, "Done", status="completed", stage="done", completed="2026-01-02"),
        ]
    )
    store = ApiStore(IssuekitConfig(api_url="https://mine.example"), client=client)

    active, completed, all_issues = store.read_all_issues()

    assert [issue.id for issue in active] == [1]
    assert [issue.id for issue in completed] == [2]
    assert [issue.id for issue in all_issues] == [1, 2]
    assert [issue.id for issue in store.find_for("claude", "review")] == [1]
    assert client.list_calls == [
        {"status": None, "assignee": None, "stage": None, "include_completed": True},
        {"status": None, "assignee": "claude", "stage": "review", "include_completed": False},
    ]


def test_api_store_reads_all_pages() -> None:
    active = [api_issue(issue_id, f"Active {issue_id}") for issue_id in range(1, 526)]
    completed = [
        api_issue(
            issue_id,
            f"Done {issue_id}",
            status="completed",
            stage="done",
            completed="2026-01-02",
        )
        for issue_id in range(526, 1051)
    ]
    client = FakeIssuekitClient(active + completed)
    store = ApiStore(IssuekitConfig(api_url="https://mine.example"), client=client)

    active_issues, all_completed_issues, all_issues = store.read_all_issues()

    assert len(active_issues) == 525
    assert len(all_completed_issues) == 525
    assert len(all_issues) == 1050
    assert [issue.id for issue in all_completed_issues[:2]] == [526, 527]
    assert all_completed_issues[-1].id == 1050
