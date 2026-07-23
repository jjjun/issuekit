from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit.guards.author import read_author_guard
from issuekit.commands.approve import approve_issue
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue


def test_queue_command_uses_api_store_when_configured(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "Review", status="in_progress", assignee="claude", stage="review"),
            api_issue(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["queue", "--assignee", "claude", "--stage", "review"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=1 ref=demo#1 assignee=claude stage=review" in captured.out
    assert "id=2" not in captured.out


def test_queue_command_marks_waiting_dependencies(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "Blocked", assignee="codex", dependency_state="waiting")]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["queue", "--assignee", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "dependency_state=waiting" in captured.out


def test_author_command_uses_api_allocated_id(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "API Write",
            "--body",
            "## Problem\n\nUse the API.",
            "--priority",
            "high",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Authored issue: demo#1" in captured.out
    assert "API validation passed" not in captured.out
    guard = read_author_guard(tmp_path)
    assert guard is not None
    assert client.calls[0] == {
        "method": "create_issue",
        "body": {
            "title": "API Write",
            "body": "## Problem\n\nUse the API.",
            "priority": "high",
            "author": "codex",
            "session": guard.author_session,
        },
    }
    assert not (tmp_path / "docs" / "issues" / "active").exists()


def test_claim_command_uses_api_claim_next(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", priority="high", author="claude")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--assignee", "codex", "--priority", "high"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=5 ref=demo#5 assignee=codex stage=implementing" in captured.out
    assert client.calls == [
        {"method": "claim_next", "body": {"assignee": "codex", "priority": "high"}}
    ]


def test_claim_command_uses_default_implementer_when_assignee_is_omitted(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", author="codex")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n"
        "default_implementer = 'claude'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["claim"]) == 0
    assert "assignee=claude" in capsys.readouterr().out


def test_claim_command_requires_assignee_without_default_implementer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", author="claude")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["claim"]) == 1
    assert "No implementer is configured" in capsys.readouterr().err
    assert client.calls == []


def test_claim_command_can_claim_specific_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", priority="high", author="claude")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--id", "5", "--assignee", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=5 ref=demo#5 assignee=codex stage=implementing" in captured.out
    assert client.calls == [{"method": "claim", "number": 5, "body": {"assignee": "codex"}}]


def test_claim_command_prints_dependency_warning_from_explicit_claim(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                5,
                "Waiting",
                priority="high",
                author="claude",
                dependency_state="waiting",
            )
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--id", "5", "--assignee", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=5 ref=demo#5 assignee=codex stage=implementing" in captured.out
    assert "dependency_state=waiting" in captured.err


def test_claim_command_allow_any_branch_bypasses_work_branch_guard(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", priority="high", author="claude")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\nwork_branch = 'main'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        ["claim", "--id", "5", "--assignee", "codex", "--allow-any-branch", "--no-sync"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=5 ref=demo#5 assignee=codex stage=implementing" in captured.out
    assert client.calls == [{"method": "claim", "number": 5, "body": {"assignee": "codex"}}]


def test_claim_command_no_sync_bypasses_claim_sync_guard(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(5, "Ready", priority="high", author="claude")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\nwork_branch = 'main'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "?? debris.txt")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--id", "5", "--assignee", "codex", "--no-sync"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=5 ref=demo#5 assignee=codex stage=implementing" in captured.out
    assert client.calls == [{"method": "claim", "number": 5, "body": {"assignee": "codex"}}]


def test_claim_command_rejects_priority_with_specific_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--id", "5", "--assignee", "codex", "--priority", "high"])

    assert exit_code == 1
    assert "--priority can only be used" in capsys.readouterr().err


def test_handoff_commands_use_api_structured_params_without_local_notes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    body = "# Issue #9: API Flow\n\nOriginal body.\n"
    client = FakeIssuekitClient(
        [
            api_issue(
                9,
                "API Flow",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
                body=body,
            )
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        [
            "submit-review",
            "9",
            "--summary",
            "Implemented.",
            "--branch",
            "main",
            "--commit",
            "abc123",
        ]
    )
    request_exit = cli.main(["request-changes", "9", "--notes", "Add tests."])

    captured = capsys.readouterr()
    assert submit_exit == 0
    assert request_exit == 0
    assert "id=9 ref=demo#9 assignee= stage=review" in captured.out
    assert "id=9 ref=demo#9 assignee=codex stage=changes_requested" in captured.out
    assert client.calls == [
        {
            "method": "submit",
            "number": 9,
            "body": {"summary": "Implemented.", "branch": "main", "commit": "abc123"},
        },
        {"method": "request_changes", "number": 9, "body": {"notes": "Add tests."}},
    ]
    assert client.get_issue(9)["body"] == body


def test_api_approval_and_completion_pass_structured_params(
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                9,
                "API Flow",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                author="claude",
            ),
            api_issue(
                10,
                "Direct Complete",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
            ),
        ]
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="demo",
        default_reviewer="auto",
        require_distinct_reviewer=True,
    )

    approved = approve_issue(
        9,
        summary="Looks good.",
        verification="uv run pytest",
        config=config,
    )
    completed = complete_issue(
        10,
        summary="Done.",
        verification="manual",
        force=True,
        config=config,
    )

    assert approved.stage == "done"
    assert completed.stage == "done"
    assert client.calls == [
        {
            "method": "approve",
            "number": 9,
            "body": {
                "summary": "Looks good.",
                "verification": "uv run pytest",
                "reviewer": "claude",
            },
        },
        {
            "method": "complete",
            "number": 10,
            "body": {"summary": "Done.", "verification": "manual", "force": True},
        },
    ]


def test_api_server_rejected_transition_surfaces_workflow_error(
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                9,
                "API Flow",
                status="in_progress",
                assignee="codex",
                stage="review",
                implementer="codex",
                author="claude",
            )
        ]
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    config = IssuekitConfig(api_url="https://mine.example", project="demo")

    with pytest.raises(WorkflowError, match="self-review is not allowed") as excinfo:
        approve_issue(
            9,
            verification="uv run pytest",
            reviewer="codex",
            config=config,
        )

    assert excinfo.value.code == "invalid_transition"


def test_submit_review_rejects_non_ascii_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: FakeIssuekitClient())
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["submit-review", "1", "--summary", "\u3042"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err


def test_handoff_commands_reject_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: FakeIssuekitClient())
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        ["submit-review", "bad-id", "--summary", "Implemented."],
    )
    request_exit = cli.main(["request-changes", "bad-id", "--notes", "Add tests."])

    assert submit_exit == 1
    assert request_exit == 1
    out = capsys.readouterr()
    assert "Invalid issue id: bad-id" in out.err
