import pytest

from issuekit import store as store_module
from issuekit.author_guard import (
    ENFORCE_AUTHOR_HANDOFF_ENV,
    create_author_guard,
    read_author_guard,
)
from issuekit.commands.approve import approve_issue
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig, WorkerIdentity
from issuekit.core import Issue
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import (
    WorkflowError,
    claim_issue,
    claim_next,
    find_for,
    request_changes,
    resolve_reviewer,
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

    with pytest.raises(WorkflowError, match="self-implementation is not allowed") as excinfo:
        claim_issue(1, "codex", config=_config(client, monkeypatch))

    message = str(excinfo.value)
    assert "Guard: server author-implementer guard (mine-py)." in message
    assert "`--allow-author-session` does not bypass it" in message
    assert "issuekit#162 and issuekit#163" in message


def test_author_guard_blocks_claim_in_same_checkout(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    with pytest.raises(WorkflowError, match="STOP_NOW"):
        claim_issue(1, "codex", config=config, cwd=tmp_path)

    issue = claim_issue(
        1,
        "codex",
        config=config,
        cwd=tmp_path,
        allow_author_guard_override=True,
    )
    assert issue.id == 1


def test_issue_author_guard_blocks_claim_next_in_same_checkout(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(2, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    with pytest.raises(WorkflowError, match="Author-session guard blocks claim-next"):
        claim_next("codex", config=config, cwd=tmp_path)

    assert client.calls == []


def test_issue_author_guard_allows_claim_for_unrelated_issue(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Authored", author="codex"),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    issue = claim_issue(2, "codex", config=config, cwd=tmp_path)

    assert issue.id == 2
    assert client.calls == [
        {"method": "claim", "number": 2, "body": {"assignee": "codex"}}
    ]


def test_proposal_author_guard_does_not_block_claim_next(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="proposal",
        item_id=208,
        ref="mine-py#208",
        target_project="mine-py",
        author_agent="codex",
    )

    issue = claim_next("codex", config=config, cwd=tmp_path)

    assert issue is not None
    assert issue.id == 1
    assert read_author_guard(tmp_path) is not None
    assert client.calls == [{"method": "claim_next", "body": {"assignee": "codex"}}]


def test_proposal_author_guard_does_not_block_claim_issue(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="proposal",
        item_id=208,
        ref="mine-py#208",
        target_project="mine-py",
        author_agent="codex",
    )

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.id == 1
    assert read_author_guard(tmp_path) is not None


def test_work_branch_guard_blocks_claim_before_api_call(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    config = IssuekitConfig(
        api_url=config.api_url,
        project=config.project,
        work_branch="main",
    )
    monkeypatch.setattr("issuekit.branch_guard.git_current_branch", lambda cwd: "feature")

    with pytest.raises(WorkflowError, match="Work-branch guard blocks claim issue #1"):
        claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert client.calls == []


def test_work_branch_guard_allows_claim_with_bypass(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = IssuekitConfig(api_url="https://mine.example", project="demo", work_branch="main")
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("issuekit.branch_guard.git_current_branch", lambda cwd: "feature")

    issue = claim_issue(
        1,
        "codex",
        config=config,
        cwd=tmp_path,
        allow_any_branch=True,
    )

    assert issue.id == 1
    assert client.calls == [
        {"method": "claim", "number": 1, "body": {"assignee": "codex"}}
    ]


@pytest.mark.parametrize(
    ("env_value", "guard_present", "should_block"),
    [
        (None, False, False),
        (None, True, True),
        ("1", False, False),
        ("1", True, True),
        ("true", False, False),
        ("true", True, True),
        ("yes", False, False),
        ("yes", True, True),
        ("on", False, False),
        ("on", True, True),
        ("0", False, False),
        ("0", True, False),
        ("false", False, False),
        ("false", True, False),
        ("no", False, False),
        ("no", True, False),
        ("off", False, False),
        ("off", True, False),
        ("", True, True),
        ("   ", True, True),
        ("maybe", True, True),
    ],
)
def test_author_guard_enforcement_env_matrix(
    tmp_path,
    monkeypatch,
    env_value: str | None,
    guard_present: bool,
    should_block: bool,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    if env_value is None:
        monkeypatch.delenv(ENFORCE_AUTHOR_HANDOFF_ENV, raising=False)
    else:
        monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, env_value)
    if guard_present:
        create_author_guard(
            tmp_path,
            config=config,
            kind="issue",
            item_id=1,
            ref="demo#1",
            author_agent="codex",
        )

    if should_block:
        with pytest.raises(WorkflowError, match="STOP_NOW"):
            claim_issue(1, "codex", config=config, cwd=tmp_path)
        return

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.id == 1
    if guard_present:
        assert read_author_guard(tmp_path) is not None


def test_claim_sends_allow_self_implement_when_enforcement_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, "0")
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(client, monkeypatch)

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim",
            "number": 1,
            "body": {"assignee": "codex", "allow_self_implement": True},
        }
    ]


def test_claim_next_sends_allow_self_implement_when_enforcement_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, "0")
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(client, monkeypatch)

    issue = claim_next("codex", config=config, cwd=tmp_path)

    assert issue is not None
    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "allow_self_implement": True},
        }
    ]


def test_claim_omits_allow_self_implement_when_enforcement_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, "1")
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.id == 1
    assert client.calls == [
        {"method": "claim", "number": 1, "body": {"assignee": "codex"}}
    ]


def test_author_guard_does_not_block_different_checkout(tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(client, monkeypatch)
    author_checkout = tmp_path / "author"
    implementer_checkout = tmp_path / "implementer"
    author_checkout.mkdir()
    implementer_checkout.mkdir()
    create_author_guard(
        author_checkout,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    issue = claim_issue(1, "codex", config=config, cwd=implementer_checkout)

    assert issue.id == 1


def test_author_guard_blocks_submit_for_review_for_authored_issue(tmp_path, monkeypatch) -> None:
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
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    with pytest.raises(WorkflowError, match="Author-session guard blocks submit"):
        submit_for_review(1, summary="Implemented.", config=config, cwd=tmp_path)


def test_proposal_author_guard_does_not_block_submit_for_review(tmp_path, monkeypatch) -> None:
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
    config = _config(client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="proposal",
        item_id=208,
        ref="mine-py#208",
        target_project="mine-py",
        author_agent="codex",
    )

    issue = submit_for_review(1, summary="Implemented.", config=config, cwd=tmp_path)

    assert issue.stage == "review"
    assert read_author_guard(tmp_path) is not None


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


def test_submit_for_review_defaults_branch_to_current_checkout(monkeypatch) -> None:
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
    monkeypatch.setattr("issuekit.workflow.git_current_branch", lambda cwd: "feature")

    issue = submit_for_review(
        1,
        summary="Implemented workflow.",
        config=_config(client, monkeypatch),
    )

    assert issue.stage == "review"
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {"summary": "Implemented workflow.", "branch": "feature"},
        }
    ]


def test_submit_for_review_omits_branch_when_checkout_branch_unknown(monkeypatch) -> None:
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
    monkeypatch.setattr("issuekit.workflow.git_current_branch", lambda cwd: None)

    submit_for_review(
        1,
        summary="Implemented workflow.",
        config=_config(client, monkeypatch),
    )

    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {"summary": "Implemented workflow."},
        }
    ]


def test_work_branch_guard_blocks_submit_before_api_call(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("issuekit.branch_guard.git_current_branch", lambda cwd: "feature")
    config = IssuekitConfig(api_url="https://mine.example", project="demo", work_branch="main")

    with pytest.raises(WorkflowError, match="Work-branch guard blocks submit issue #1"):
        submit_for_review(1, summary="Implemented.", config=config, cwd=tmp_path)

    assert client.calls == []


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


def test_distinct_reviewer_guard_names_recovery() -> None:
    config = IssuekitConfig(
        assignees=("codex",),
        default_reviewer="auto",
        require_distinct_reviewer=True,
    )
    issue = Issue(
        id=1,
        ref="demo#1",
        title="First",
        issue_status="in_progress",
        created="2026-01-01",
        completed="",
        priority="medium",
        assignee="",
        stage="review",
        implementer="codex",
        author="claude",
        body="",
        metadata={},
    )

    with pytest.raises(WorkflowError) as excinfo:
        resolve_reviewer(None, config, issue=issue)

    message = str(excinfo.value)
    assert "Distinct-reviewer guard (require_distinct_reviewer)" in message
    assert "no configured reviewer is distinct from the issue implementer" in message
    assert "configure an assignee distinct from issue.implementer" in message
    assert "compares against `issue.implementer`, not the author" in message


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

    assert issue.issue_status == "completed"
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
