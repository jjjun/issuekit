from pathlib import Path

import pytest

from issuekit import store as store_module
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import (
    WorkflowError,
    claim_issue,
    claim_next,
    find_for,
    request_changes,
    submit_for_review,
)

from tests.issue_helpers import api_issue


def _config(client: FakeIssuekitClient, monkeypatch) -> IssuekitConfig:
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    return IssuekitConfig(api_url="https://mine.example", project="demo")


def test_claim_next_routes_to_api_and_picks_highest_priority(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Low", priority="low"),
            api_issue(2, "High", priority="high"),
            api_issue(3, "Planned", status="planned"),
        ]
    )

    issue = claim_next(tmp_path / "docs" / "issues", "codex", config=_config(client, monkeypatch))

    assert issue is not None
    assert issue.id == 2
    assert issue.issue_status == "in_progress"
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"
    assert client.calls == [{"method": "claim_next", "body": {"assignee": "codex"}}]


def test_claim_next_respects_priority_filter(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Low", priority="low"),
            api_issue(2, "High", priority="high"),
        ]
    )

    issue = claim_next(
        tmp_path / "docs" / "issues",
        "codex",
        priority="low",
        config=_config(client, monkeypatch),
    )

    assert issue is not None
    assert issue.id == 1
    assert client.calls == [
        {"method": "claim_next", "body": {"assignee": "codex", "priority": "low"}}
    ]


def test_claim_issue_surfaces_api_transition_error(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", assignee="codex", author="codex")])

    with pytest.raises(WorkflowError, match="self-implementation is not allowed"):
        claim_issue(tmp_path / "docs" / "issues", 1, "codex", config=_config(client, monkeypatch))


def test_submit_for_review_passes_structured_fields(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            )
        ]
    )

    issue = submit_for_review(
        tmp_path / "docs" / "issues",
        1,
        summary="Implemented workflow.",
        branch="codex/workflow",
        commit="abc123",
        config=_config(client, monkeypatch),
    )

    assert issue.assignee == ""
    assert issue.stage == "review"
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {
                "summary": "Implemented workflow.",
                "branch": "codex/workflow",
                "commit": "abc123",
            },
        }
    ]


def test_request_changes_returns_issue_to_implementer(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
            )
        ]
    )

    issue = request_changes(
        tmp_path / "docs" / "issues",
        1,
        notes="Please add tests.",
        config=_config(client, monkeypatch),
    )

    assert issue.assignee == "codex"
    assert issue.stage == "changes_requested"
    assert client.calls == [
        {"method": "request_changes", "number": 1, "body": {"notes": "Please add tests."}}
    ]


def test_find_for_lists_matching_active_issues(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Review", status="in_progress", assignee="claude", stage="review"),
            api_issue(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
        ]
    )

    issues = find_for(
        tmp_path / "docs" / "issues",
        "claude",
        stage="review",
        config=_config(client, monkeypatch),
    )

    assert [issue.id for issue in issues] == [1]


def test_complete_issue_uses_api_complete(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="review")])

    issue = complete_issue(
        tmp_path / "docs" / "issues",
        1,
        summary="Approved.",
        verification="pytest",
        config=_config(client, monkeypatch),
    )

    assert issue.status == "completed"
    assert issue.stage == "done"
    assert client.calls == [
        {
            "method": "complete",
            "number": 1,
            "body": {"summary": "Approved.", "verification": "pytest", "force": False},
        }
    ]


def test_workflow_rejects_invalid_tokens_and_non_ascii_text(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    config = _config(client, monkeypatch)

    with pytest.raises(WorkflowError, match="Invalid assignee token"):
        claim_next(tmp_path / "docs" / "issues", "codex\nstage: done", config=config)
    with pytest.raises(WorkflowError, match="ASCII-only"):
        submit_for_review(tmp_path / "docs" / "issues", 1, summary="\u3042", config=config)
