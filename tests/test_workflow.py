import pytest

from issuekit import store as store_module
from issuekit.commands.approve import approve_issue
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig, WorkerIdentity
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


def _config(
    client: FakeIssuekitClient,
    monkeypatch,
    *,
    worker: WorkerIdentity | None = None,
) -> IssuekitConfig:
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    return IssuekitConfig(api_url="https://mine.example", project="demo", worker=worker)


def test_claim_next_routes_to_api_and_picks_highest_priority(monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Low", priority="low"),
            api_issue(2, "High", priority="high"),
            api_issue(3, "Planned", status="planned"),
        ]
    )

    issue = claim_next("codex", config=_config(client, monkeypatch))

    assert issue is not None
    assert issue.id == 2
    assert issue.issue_status == "in_progress"
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"
    assert client.calls == [{"method": "claim_next", "body": {"assignee": "codex"}}]


def test_claim_next_respects_priority_filter(monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Low", priority="low"),
            api_issue(2, "High", priority="high"),
        ]
    )

    issue = claim_next(
        "codex",
        priority="low",
        config=_config(client, monkeypatch),
    )

    assert issue is not None
    assert issue.id == 1
    assert client.calls == [
        {"method": "claim_next", "body": {"assignee": "codex", "priority": "low"}}
    ]


def test_claim_next_sends_registered_worker(monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )

    issue = claim_next("codex", config=config)

    assert issue is not None
    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "worker": "machine/repo/checkout"},
        }
    ]


def test_claim_issue_sends_registered_worker(monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )

    issue = claim_issue(1, "codex", config=config)

    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim",
            "number": 1,
            "body": {"assignee": "codex", "worker": "machine/repo/checkout"},
        }
    ]


def test_claim_issue_surfaces_api_transition_error(monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", assignee="codex", author="codex")])

    with pytest.raises(WorkflowError, match="self-implementation is not allowed"):
        claim_issue(1, "codex", config=_config(client, monkeypatch))


def test_submit_for_review_passes_structured_fields(monkeypatch) -> None:
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


def test_request_changes_returns_issue_to_implementer(monkeypatch) -> None:
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
        1,
        notes="Please add tests.",
        config=_config(client, monkeypatch),
    )

    assert issue.assignee == "codex"
    assert issue.stage == "changes_requested"
    assert client.calls == [
        {"method": "request_changes", "number": 1, "body": {"notes": "Please add tests."}}
    ]


def test_request_changes_sends_registered_reviewer_worker(monkeypatch) -> None:
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
    config = _config(
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "reviewer"),
    )

    request_changes(
        1,
        notes="Please add tests.",
        config=config,
    )

    assert client.calls == [
        {
            "method": "request_changes",
            "number": 1,
            "body": {"notes": "Please add tests.", "worker": "machine/repo/reviewer"},
        }
    ]


def test_approve_rejects_same_agent_same_worker_review(monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/repo/checkout",
            )
        ]
    )
    config = _config(
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )

    with pytest.raises(WorkflowError, match="self-review is not allowed"):
        approve_issue(
            1,
            verification="uv run pytest",
            reviewer="codex",
            config=config,
        )

    assert client.get_issue(1)["status"] == "in_progress"


def test_approve_allows_same_agent_different_worker_open_review(
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/repo/implementer",
            )
        ]
    )
    config = _config(
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "reviewer"),
    )

    issue = approve_issue(
        1,
        verification="uv run pytest",
        reviewer="codex",
        config=config,
    )

    assert issue.issue_status == "completed"
    assert client.calls[-1] == {
        "method": "approve",
        "number": 1,
        "body": {
            "summary": "Approved.",
            "verification": "uv run pytest",
            "reviewer": "codex",
            "worker": "machine/repo/reviewer",
        },
    }


def test_approve_allows_different_agent_review(monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
                worker="machine/repo/implementer",
            )
        ]
    )
    config = _config(
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "reviewer"),
    )

    issue = approve_issue(
        1,
        verification="uv run pytest",
        reviewer="claude",
        config=config,
    )

    assert issue.issue_status == "completed"


def test_approve_rejects_unassigned_reviewer_before_api_transition(
    monkeypatch,
) -> None:
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
    config = _config(client, monkeypatch)

    with pytest.raises(WorkflowError) as excinfo:
        approve_issue(
            1,
            verification="uv run pytest",
            reviewer="codex",
            config=config,
        )

    message = str(excinfo.value)
    assert "review is assigned to reviewer 'claude'" in message
    assert "You passed reviewer='codex'" in message
    assert client.calls == []


def test_find_for_lists_matching_active_issues(monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Review", status="in_progress", assignee="claude", stage="review"),
            api_issue(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
        ]
    )

    issues = find_for(
        "claude",
        stage="review",
        config=_config(client, monkeypatch),
    )

    assert [issue.id for issue in issues] == [1]


def test_complete_issue_uses_api_complete(monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="review")])

    issue = complete_issue(
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


def test_workflow_rejects_invalid_tokens_and_non_ascii_text(monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    config = _config(client, monkeypatch)

    with pytest.raises(WorkflowError, match="Invalid assignee token"):
        claim_next("codex\nstage: done", config=config)
    with pytest.raises(WorkflowError, match="ASCII-only"):
        submit_for_review(1, summary="\u3042", config=config)
