import pytest

from issuekit.config import IssuekitConfig, WorkerIdentity
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.guards.author import (
    ENFORCE_AUTHOR_HANDOFF_ENV,
    AuthorOrchestrationContext,
    create_author_guard,
    read_author_guards,
)
from issuekit.issues.service import approve_issue, complete_issue
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import (
    claim_issue,
    claim_next,
    find_for,
    readdress_issue,
    reclaim_issue,
    request_changes,
    resolve_implementer,
    resolve_reviewer,
    submit_for_review,
)
from tests.issue_helpers import api_issue


def _config(
    fake_api,
    client: FakeIssuekitClient,
    monkeypatch,
    *,
    worker: WorkerIdentity | None = None,
) -> IssuekitConfig:
    fake_api.install_client(client)
    return IssuekitConfig(api_url="https://mine.example", project="demo", worker=worker)


def test_claim_next_routes_to_api_and_picks_highest_priority(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Low", priority="low"),
            api_issue(2, "High", priority="high"),
            api_issue(3, "Planned", status="planned"),
        ]
    )

    issue = claim_next("codex", config=_config(fake_api, client, monkeypatch))

    assert issue is not None
    assert issue.id == 2
    assert issue.issue_status == "in_progress"
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"
    assert client.calls == [{"method": "claim_next", "body": {"assignee": "codex"}}]


def test_claim_next_respects_priority_filter(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Low", priority="low"),
            api_issue(2, "High", priority="high"),
        ]
    )

    issue = claim_next(
        "codex",
        priority="low",
        config=_config(fake_api, client, monkeypatch),
    )

    assert issue is not None
    assert issue.id == 1
    assert client.calls == [
        {"method": "claim_next", "body": {"assignee": "codex", "priority": "low"}}
    ]


def test_claim_next_sends_registered_worker(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(
        fake_api,
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
            "body": {"assignee": "codex", "worker": "checkout.repo@machine"},
        }
    ]


def test_claim_issue_sends_registered_worker(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(
        fake_api,
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
            "body": {"assignee": "codex", "worker": "checkout.repo@machine"},
        }
    ]


def test_claim_next_filters_directed_issues_to_target_worker(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Directed", author="claude", target_worker="checkout.repo"),
            api_issue(2, "Pool", author="claude"),
        ]
    )
    other_config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "other"),
    )

    wrong_worker_issue = claim_next("codex", config=other_config)

    assert wrong_worker_issue is not None
    assert wrong_worker_issue.id == 2
    target_config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )

    directed_issue = claim_next("codex", config=target_config)

    assert directed_issue is not None
    assert directed_issue.id == 1
    assert client.calls == [
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "worker": "other.repo@machine"},
        },
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "worker": "checkout.repo@machine"},
        },
    ]


def test_claim_next_machine_qualified_target_requires_matching_machine(
    fake_api,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Directed",
                author="claude",
                target_worker="checkout.repo@pike3",
            )
        ]
    )
    other_machine_config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("main1", "repo", "checkout"),
    )

    assert claim_next("codex", config=other_machine_config) is None

    same_machine_config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("pike3", "repo", "checkout"),
    )

    directed_issue = claim_next("codex", config=same_machine_config)

    assert directed_issue is not None
    assert directed_issue.id == 1


def test_claim_issue_rejects_wrong_directed_worker(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Directed", author="claude", target_worker="checkout.repo")]
    )
    config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "other"),
    )

    with pytest.raises(WorkflowError, match="directed to worker checkout.repo"):
        claim_issue(1, "codex", config=config)


def test_workflow_claim_submit_review_actions_send_configured_session(
    fake_api,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ISSUEKIT_SESSION", "sess-1")
    client = FakeIssuekitClient(
        [
            api_issue(1, "Ready", author="claude"),
            api_issue(
                2,
                "Review",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
            ),
            api_issue(
                3,
                "Changes",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
            ),
            api_issue(
                4,
                "Approve",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
            ),
        ]
    )
    config = _config(fake_api, client, monkeypatch)

    claim_issue(1, "codex", config=config)
    submit_for_review(2, summary="Implemented.", config=config)
    request_changes(3, notes="Add tests.", reviewer="claude", config=config)
    approve_issue(4, verification="uv run pytest", reviewer="claude", config=config)

    assert [call["body"]["session"] for call in client.calls] == [
        "sess-1",
        "sess-1",
        "sess-1",
        "sess-1",
    ]


def test_reclaim_issue_sends_registered_worker_as_actor(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Stuck",
                status="in_progress",
                stage="implementing",
                assignee="claude",
                implementer="claude",
                worker="machine/repo/dead",
            )
        ]
    )
    config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "operator"),
    )

    result = reclaim_issue(1, force=True, reason="stale checkout", config=config)

    assert result.actor == "operator.repo"
    assert result.audit_reason == "stale checkout"
    assert result.issue.stage == "todo"
    assert client.calls == [
        {
            "method": "reclaim",
            "number": 1,
            "body": {
                "expected_worker": "machine/repo/dead",
                "actor": "operator.repo",
                "reason": "stale checkout",
            },
        }
    ]


def test_readdress_issue_returns_directed_issue_to_pool(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Directed", author="claude", target_worker="checkout.repo")]
    )
    config = _config(
        fake_api,
        client,
        monkeypatch,
        worker=WorkerIdentity("machine", "repo", "operator"),
    )

    result = readdress_issue(1, reason="stale directed checkout", config=config)

    assert result.previous.target_worker == "checkout.repo"
    assert result.issue.target_worker == ""
    assert result.actor == "operator.repo"
    assert client.get_issue(1)["target_worker"] == ""
    assert client.calls == [
        {
            "method": "readdress",
            "number": 1,
            "body": {
                "expected_target_worker": "checkout.repo",
                "actor": "operator.repo",
                "reason": "stale directed checkout",
            },
        }
    ]


def test_reclaim_issue_rejects_non_ascii_reason(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Stuck",
                status="in_progress",
                stage="implementing",
                worker="machine/repo/dead",
            )
        ]
    )

    with pytest.raises(WorkflowError, match="--reason must be ASCII-only"):
        reclaim_issue(1, force=True, reason="stale \u2603", config=_config(fake_api, client, monkeypatch))

    assert client.calls == []


def test_claim_issue_surfaces_api_transition_error(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", assignee="codex", author="codex")])

    with pytest.raises(WorkflowError, match="Same-name implementation is allowed only") as excinfo:
        claim_issue(1, "codex", config=_config(fake_api, client, monkeypatch))

    assert excinfo.value.code == "forbidden_self_implement"
    message = str(excinfo.value)
    assert "Guard: server author-implementer guard (mine-py)." in message
    assert "`--allow-author-session` does not bypass it" in message
    assert "issuekit#162 and issuekit#163" not in message


def test_forbidden_self_implement_code_adds_server_guard_note() -> None:
    error = WorkflowError(
        "The server rejected this request.", code="forbidden_self_implement"
    )

    assert "server author-implementer guard (mine-py)" in str(error)


def test_fake_claim_allows_same_name_changes_continuation() -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Changes",
                status="in_progress",
                assignee="codex",
                stage="changes_requested",
                implementer="codex",
                author="codex",
            )
        ]
    )

    issue = client.claim(1, assignee="codex")

    assert issue["stage"] == "implementing"
    assert issue["implementer"] == "codex"


def test_fake_claim_next_allows_open_pool_same_name_without_author_session() -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])

    issue = client.claim_next(assignee="codex")

    assert issue is not None
    assert issue["id"] == 1
    assert issue["assignee"] == "codex"
    assert issue["stage"] == "implementing"


def test_fake_claim_rejects_assigned_same_name_without_sessions() -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Assigned", assignee="codex", author="codex")]
    )

    with pytest.raises(WorkflowError) as excinfo:
        client.claim(1, assignee="codex")

    assert excinfo.value.code == "forbidden_self_implement"


def test_fake_claim_allows_distinct_author_and_implementer_sessions() -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Ready", author="codex")
            | {"author_session": "author-session"}
        ]
    )

    issue = client.claim(1, assignee="codex", session="implementer-session")

    assert issue["stage"] == "implementing"
    assert issue["implementer_session"] == "implementer-session"


def test_author_guard_blocks_claim_in_same_checkout(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
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


def test_legacy_author_guard_table_refuses_claim(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[author_guard]\n"
            'project = "demo"\n'
            'kind = "issue"\n'
            'id = "1"\n'
            'ref = "demo#1"\n'
            'author_agent = "codex"\n'
            'required_next_action = "STOP"\n'
            "\n"
            "[refs]\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(WorkflowError, match=r"removed \[author_guard\] table"):
        claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert client.calls == []


def test_author_guard_allows_orchestrated_claim_for_distinct_agent(
    fake_api,
    tmp_path, monkeypatch
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(fake_api, client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    issue = claim_issue(
        1,
        "claude",
        config=config,
        cwd=tmp_path,
        session="run-123",
        orchestration=AuthorOrchestrationContext(
            implementer_agent="claude",
            run_session="run-123",
        ),
    )

    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim",
            "number": 1,
            "body": {"assignee": "claude", "session": "run-123"},
        }
    ]


def test_author_guard_allows_orchestrated_same_agent_with_distinct_session(
    fake_api,
    tmp_path, monkeypatch
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Ready", author="codex") | {"author_session": "author-123"}]
    )
    config = _config(fake_api, client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
        author_session="author-123",
    )

    issue = claim_issue(
        1,
        "codex",
        config=config,
        cwd=tmp_path,
        session="run-456",
        orchestration=AuthorOrchestrationContext(
            implementer_agent="codex",
            run_session="run-456",
        ),
    )

    assert issue.id == 1
    assert issue.implementer == "codex"
    assert client.calls[0]["body"] == {"assignee": "codex", "session": "run-456"}


def test_author_guard_blocks_orchestrated_same_agent_without_author_session(
    fake_api,
    tmp_path, monkeypatch
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(fake_api, client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    with pytest.raises(WorkflowError, match="authored with ISSUEKIT_SESSION"):
        claim_issue(
            1,
            "codex",
            config=config,
            cwd=tmp_path,
            session="run-456",
            orchestration=AuthorOrchestrationContext(
                implementer_agent="codex",
                run_session="run-456",
            ),
        )

    assert client.calls == []


def test_author_guard_blocks_orchestrated_unknown_author(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(fake_api, client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent=None,
    )

    with pytest.raises(WorkflowError, match="author agent is unknown"):
        claim_issue(
            1,
            "claude",
            config=config,
            cwd=tmp_path,
            session="run-456",
            orchestration=AuthorOrchestrationContext(
                implementer_agent="claude",
                run_session="run-456",
            ),
        )

    assert client.calls == []


def test_issue_author_guard_blocks_claim_next_in_same_checkout(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(2, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
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


def test_issue_author_guard_allows_claim_for_unrelated_issue(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Authored", author="codex"),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    config = _config(fake_api, client, monkeypatch)
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


def test_proposal_author_guard_does_not_block_claim_next(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
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
    assert read_author_guards(tmp_path)
    assert client.calls == [{"method": "claim_next", "body": {"assignee": "codex"}}]


def test_proposal_author_guard_does_not_block_claim_issue(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
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
    assert read_author_guards(tmp_path)


def test_work_branch_guard_blocks_claim_before_api_call(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
    config = IssuekitConfig(
        api_url=config.api_url,
        project=config.project,
        work_branch="main",
    )
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")

    with pytest.raises(WorkflowError, match="Work-branch guard blocks claim issue #1"):
        claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert client.calls == []


def test_work_branch_guard_allows_claim_with_bypass(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = IssuekitConfig(api_url="https://mine.example", project="demo", work_branch="main")
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")

    issue = claim_issue(
        1,
        "codex",
        config=config,
        cwd=tmp_path,
        allow_any_branch=True,
        no_sync=True,
    )

    assert issue.id == 1
    assert client.calls == [
        {"method": "claim", "number": 1, "body": {"assignee": "codex"}}
    ]


def test_claim_sync_guard_blocks_claim_before_api_call(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = IssuekitConfig(api_url="https://mine.example", project="demo", work_branch="main")
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "?? debris.txt")

    with pytest.raises(WorkflowError, match="Claim-sync guard blocks claim issue #1"):
        claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert client.calls == []


def test_claim_sync_guard_allows_claim_with_no_sync(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = IssuekitConfig(api_url="https://mine.example", project="demo", work_branch="main")
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "?? debris.txt")

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path, no_sync=True)

    assert issue.id == 1
    assert client.calls == [
        {"method": "claim", "number": 1, "body": {"assignee": "codex"}}
    ]


def test_claim_issue_skips_claim_sync_for_same_worker_changes_continuation(
    fake_api,
    tmp_path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Changes",
                status="in_progress",
                assignee="codex",
                stage="changes_requested",
                implementer="codex",
                author="claude",
            )
            | {"implementation_worker": "checkout.repo@machine"}
        ]
    )
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="demo",
        work_branch="main",
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")

    def fail_claim_sync(*args, **kwargs):
        raise WorkflowError("dirty tree", code="claim_sync_guard")

    monkeypatch.setattr("issuekit.workflow.enforce_claim_sync", fail_claim_sync)

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.stage == "implementing"
    assert client.calls == [
        {
            "method": "claim",
            "number": 1,
            "body": {"assignee": "codex", "worker": "checkout.repo@machine"},
        }
    ]


def test_claim_issue_enforces_claim_sync_for_other_worker_changes_continuation(
    fake_api,
    tmp_path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Changes",
                status="in_progress",
                assignee="codex",
                stage="changes_requested",
                implementer="codex",
                author="claude",
                worker="other.repo@machine",
            )
        ]
    )
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="demo",
        work_branch="main",
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")

    def fail_claim_sync(*args, **kwargs):
        raise WorkflowError("dirty tree", code="claim_sync_guard")

    monkeypatch.setattr("issuekit.workflow.enforce_claim_sync", fail_claim_sync)

    with pytest.raises(WorkflowError, match="dirty tree"):
        claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert client.calls == []


def test_claim_next_still_enforces_claim_sync_for_dirty_changes_checkout(
    fake_api,
    tmp_path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Changes",
                status="in_progress",
                assignee="codex",
                stage="changes_requested",
                implementer="codex",
                author="claude",
                worker="checkout.repo@machine",
            )
        ]
    )
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="demo",
        work_branch="main",
        worker=WorkerIdentity("machine", "repo", "checkout"),
    )
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")

    def fail_claim_sync(*args, **kwargs):
        raise WorkflowError("dirty tree", code="claim_sync_guard")

    monkeypatch.setattr("issuekit.workflow.enforce_claim_sync", fail_claim_sync)

    with pytest.raises(WorkflowError, match="dirty tree"):
        claim_next("codex", config=config, cwd=tmp_path)

    assert client.calls == []


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
    fake_api,
    tmp_path,
    monkeypatch,
    env_value: str | None,
    guard_present: bool,
    should_block: bool,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
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
        assert read_author_guards(tmp_path)


def test_claim_sends_allow_self_implement_when_enforcement_off(fake_api, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, "0")
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(fake_api, client, monkeypatch)

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim",
            "number": 1,
            "body": {"assignee": "codex", "allow_self_implement": True},
        }
    ]


def test_claim_next_sends_allow_self_implement_when_enforcement_off(fake_api, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, "0")
    client = FakeIssuekitClient([api_issue(1, "Ready", author="codex")])
    config = _config(fake_api, client, monkeypatch)

    issue = claim_next("codex", config=config, cwd=tmp_path)

    assert issue is not None
    assert issue.id == 1
    assert client.calls == [
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "allow_self_implement": True},
        }
    ]


def test_claim_omits_allow_self_implement_when_enforcement_on(fake_api, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENFORCE_AUTHOR_HANDOFF_ENV, "1")
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)

    issue = claim_issue(1, "codex", config=config, cwd=tmp_path)

    assert issue.id == 1
    assert client.calls == [
        {"method": "claim", "number": 1, "body": {"assignee": "codex"}}
    ]


def test_author_guard_does_not_block_different_checkout(fake_api, tmp_path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    config = _config(fake_api, client, monkeypatch)
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


def test_author_guard_blocks_submit_for_review_for_authored_issue(fake_api, tmp_path, monkeypatch) -> None:
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
    config = _config(fake_api, client, monkeypatch)
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


def test_author_guard_allows_orchestrated_submit_for_distinct_agent(
    fake_api,
    tmp_path, monkeypatch
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="claude",
                stage="implementing",
                implementer="claude",
                author="codex",
            )
        ]
    )
    config = _config(fake_api, client, monkeypatch)
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )

    issue = submit_for_review(
        1,
        summary="Implemented.",
        config=config,
        cwd=tmp_path,
        session="run-123",
        orchestration=AuthorOrchestrationContext(
            implementer_agent="claude",
            run_session="run-123",
        ),
    )

    assert issue.stage == "review"
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {"summary": "Implemented.", "session": "run-123"},
        }
    ]


def test_proposal_author_guard_does_not_block_submit_for_review(fake_api, tmp_path, monkeypatch) -> None:
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
    config = _config(fake_api, client, monkeypatch)
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
    assert read_author_guards(tmp_path)


def test_submit_for_review_passes_structured_fields(fake_api, monkeypatch) -> None:
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
        config=_config(fake_api, client, monkeypatch),
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


def test_submit_for_review_rejects_unknown_reviewer_before_api_transition(
    fake_api,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])

    with pytest.raises(WorkflowError, match="Unknown assignee: nobody"):
        submit_for_review(
            1,
            summary="Implemented.",
            reviewer="nobody",
            config=_config(fake_api, client, monkeypatch),
        )

    assert client.calls == []


def test_submit_for_review_defaults_branch_to_current_checkout(fake_api, monkeypatch) -> None:
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
        config=_config(fake_api, client, monkeypatch),
    )

    assert issue.stage == "review"
    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {"summary": "Implemented workflow.", "branch": "feature"},
        }
    ]


def test_submit_for_review_omits_branch_when_checkout_branch_unknown(fake_api, monkeypatch) -> None:
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
        config=_config(fake_api, client, monkeypatch),
    )

    assert client.calls == [
        {
            "method": "submit",
            "number": 1,
            "body": {"summary": "Implemented workflow."},
        }
    ]


def test_work_branch_guard_blocks_submit_before_api_call(fake_api, tmp_path, monkeypatch) -> None:
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
    fake_api.install_client(client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")
    config = IssuekitConfig(api_url="https://mine.example", project="demo", work_branch="main")

    with pytest.raises(WorkflowError, match="Work-branch guard blocks submit issue #1"):
        submit_for_review(1, summary="Implemented.", config=config, cwd=tmp_path)

    assert client.calls == []


def test_request_changes_returns_issue_to_implementer(fake_api, monkeypatch) -> None:
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
        config=_config(fake_api, client, monkeypatch),
    )

    assert issue.assignee == "codex"
    assert issue.stage == "changes_requested"
    assert client.calls == [
        {"method": "request_changes", "number": 1, "body": {"notes": "Please add tests."}}
    ]


def test_request_changes_sends_registered_reviewer_worker(fake_api, monkeypatch) -> None:
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
        fake_api,
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
            "body": {"notes": "Please add tests.", "worker": "reviewer.repo"},
        }
    ]


def test_approve_rejects_same_agent_same_worker_review(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="checkout.repo@machine",
            )
        ]
    )
    config = _config(
        fake_api,
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
    assert "Distinct-reviewer guard blocks auto reviewer resolution" in message
    assert "no configured reviewer is distinct from the issue implementer" in message
    assert "configure an assignee distinct from issue.implementer" in message
    assert excinfo.value.code == "distinct_reviewer_guard"
    assert "compares against `issue.implementer`, not the author" in message


def test_resolve_implementer_uses_explicit_then_configured_then_single_assignee() -> None:
    config = IssuekitConfig(
        assignees=("codex", "claude"),
        default_implementer="claude",
    )

    assert resolve_implementer("codex", config) == "codex"
    assert resolve_implementer(None, config) == "claude"
    assert resolve_implementer(None, IssuekitConfig(assignees=("kimi",))) == "kimi"
    assert resolve_implementer(None, IssuekitConfig(assignees=("codex", "claude"))) is None


def test_approve_allows_same_agent_different_worker_open_review(
    fake_api,
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
        fake_api,
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
            "worker": "reviewer.repo",
        },
    }


def test_approve_allows_different_agent_review(fake_api, monkeypatch) -> None:
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
        fake_api,
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
    fake_api,
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
    config = _config(fake_api, client, monkeypatch)

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


def test_find_for_lists_matching_active_issues(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Review", status="in_progress", assignee="claude", stage="review"),
            api_issue(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
        ]
    )

    issues = find_for(
        "claude",
        stage="review",
        config=_config(fake_api, client, monkeypatch),
    )

    assert [issue.id for issue in issues] == [1]


def test_complete_issue_uses_api_complete(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="review")])

    issue = complete_issue(
        1,
        summary="Approved.",
        verification="pytest",
        config=_config(fake_api, client, monkeypatch),
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


def test_workflow_rejects_invalid_tokens_and_non_ascii_text(fake_api, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    config = _config(fake_api, client, monkeypatch)

    with pytest.raises(WorkflowError, match="Invalid assignee token"):
        claim_next("codex\nstage: done", config=config)
    with pytest.raises(WorkflowError, match="ASCII-only"):
        submit_for_review(1, summary="\u3042", config=config)
