from pathlib import Path

from issuekit.config import IssuekitConfig
from issuekit.core import issue_dict
from issuekit.store import ApiStore, get_store
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue


def test_get_store_requires_api_url_for_runtime_default(tmp_path: Path) -> None:
    try:
        get_store(IssuekitConfig(), tmp_path / "docs" / "issues")
    except WorkflowError as exc:
        assert exc.code == "missing_api_url"
    else:
        raise AssertionError("get_store should require api_url by default")


def test_get_store_uses_api_when_api_url_is_set() -> None:
    store = get_store(IssuekitConfig(api_url="https://mine.example"))

    assert isinstance(store, ApiStore)


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
                body=body,
            )
        ]
    )
    store = ApiStore(IssuekitConfig(api_url="https://mine.example", project="demo"), client=client)

    issue = store.get_issue(7)

    assert issue is not None
    assert issue.id == 7
    assert issue.file_name_id == 7
    assert issue.file_name == "demo#7"
    assert issue.file_path == Path("demo#7")
    assert issue.relative_path == "demo#7"
    assert issue.status == "active"
    assert issue.issue_status == "in_progress"
    assert issue.priority == "high"
    assert issue.assignee == "claude"
    assert issue.stage == "review"
    assert issue.implementer == "codex"
    assert issue.author == "kimi"
    assert issue.content == body
    assert issue.frontmatter.body == body
    assert issue.frontmatter.data["status"] == "in_progress"
    assert issue.decode_error is False
    assert issue_dict(issue, include_body=True) == {
        "id": 7,
        "title": "Read Path",
        "status": "in_progress",
        "assignee": "claude",
        "stage": "review",
        "implementer": "codex",
        "author": "kimi",
        "file": "demo#7",
        "body": body,
    }


def test_api_store_partitions_and_filters_active_issues() -> None:
    client = FakeIssuekitClient(
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
    assert [issue.id for issue in store.read_active_issues()] == [1]
    assert [issue.id for issue in store.read_completed_issues()] == [2]
    assert [issue.id for issue in store.find_for("claude", "review")] == [1]


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

    completed_issues = store.read_completed_issues()
    active_issues, all_completed_issues, all_issues = store.read_all_issues()

    assert len(completed_issues) == 525
    assert [issue.id for issue in completed_issues[:2]] == [526, 527]
    assert completed_issues[-1].id == 1050
    assert len(active_issues) == 525
    assert len(all_completed_issues) == 525
    assert len(all_issues) == 1050
