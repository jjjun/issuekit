from pathlib import Path

from issuekit.config import IssuekitConfig
from issuekit.core import issue_dict
from issuekit.store import ApiStore, FilesystemStore, get_store
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue, issue_text, write_issue


def test_get_store_requires_api_url_for_runtime_default(tmp_path: Path) -> None:
    try:
        get_store(IssuekitConfig(use_filesystem_store=False), tmp_path / "docs" / "issues")
    except WorkflowError as exc:
        assert exc.code == "missing_api_url"
    else:
        raise AssertionError("get_store should require api_url by default")


def test_get_store_uses_filesystem_with_explicit_escape_hatch(tmp_path: Path) -> None:
    store = get_store(IssuekitConfig(use_filesystem_store=True), tmp_path / "docs" / "issues")

    assert isinstance(store, FilesystemStore)


def test_get_store_uses_api_when_api_url_is_set() -> None:
    store = get_store(IssuekitConfig(api_url="https://mine.example"))

    assert isinstance(store, ApiStore)


def test_filesystem_store_wraps_existing_read_behavior(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))

    store = FilesystemStore(issues_dir)

    assert store.get_issue(1).relative_path == "active/001_first.md"
    assert [issue.id for issue in store.find_for()] == [1]


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
    assert [issue.id for issue in store.find_for("claude", "review")] == [1]
