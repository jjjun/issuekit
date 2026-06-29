from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit.commands.approve import approve_issue
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue, issue_text, write_indexes, write_issue


def assert_single_frontmatter_body_gap(content: str) -> None:
    assert "\n---\n\n# Issue" in content
    assert "\n---\n\n\n" not in content


def test_claim_command_claims_issue_and_updates_indexes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["claim", "--assignee", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=1" in captured.out
    assert "assignee=codex stage=implementing" in captured.out
    assert "in_progress" in (issues_dir / "indexes" / "active.md").read_text(encoding="utf-8")
    assert_single_frontmatter_body_gap(
        (issues_dir / "active" / "001_first.md").read_text(encoding="utf-8")
    )
    assert cli.main(["validate"]) == 0


def test_handoff_commands_round_trip_issue(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        [
            "submit-review",
            "1",
            "--summary",
            "Implemented.",
            "--branch",
            "codex/test",
            "--commit",
            "abc123",
        ]
    )
    request_exit = cli.main(["request-changes", "1", "--notes", "Add tests."])

    captured = capsys.readouterr()
    content = (issues_dir / "active" / "001_first.md").read_text(encoding="utf-8")
    assert submit_exit == 0
    assert request_exit == 0
    assert "assignee=claude stage=review" in captured.out
    assert "assignee=codex stage=changes_requested" in captured.out
    assert_single_frontmatter_body_gap(content)
    assert "## Handoff" in content
    assert "## Review Feedback" in content
    assert cli.main(["validate"]) == 0


def test_handoff_commands_accept_reviewer(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="claude",
            stage="implementing",
            implementer="claude",
        ),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        [
            "submit-review",
            "1",
            "--summary",
            "Implemented.",
            "--assignee",
            "claude",
            "--reviewer",
            "codex",
        ]
    )
    request_exit = cli.main(["request-changes", "1", "--notes", "Add tests.", "--reviewer", "codex"])

    captured = capsys.readouterr()
    assert submit_exit == 0
    assert request_exit == 0
    assert "assignee=codex stage=review" in captured.out
    assert "assignee=claude stage=changes_requested" in captured.out


def test_queue_command_lists_matching_issues(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_review.md",
        issue_text(1, "Review", status="in_progress", assignee="claude", stage="review"),
    )
    write_issue(
        issues_dir / "active" / "002_work.md",
        issue_text(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["queue", "--assignee", "claude", "--stage", "review"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id=1" in captured.out
    assert "id=2" not in captured.out


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
    assert "id=1 file=demo#1 assignee=claude stage=review" in captured.out
    assert "id=2" not in captured.out


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
    assert "API validation passed (1 issues)." in captured.out
    assert client.calls[0] == {
        "method": "create_issue",
        "body": {
            "title": "API Write",
            "body": "## Problem\n\nUse the API.",
            "priority": "high",
            "author": "codex",
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
    assert "id=5 file=demo#5 assignee=codex stage=implementing" in captured.out
    assert client.calls == [
        {"method": "claim_next", "body": {"assignee": "codex", "priority": "high"}}
    ]


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
    assert "id=9 file=demo#9 assignee= stage=review" in captured.out
    assert "id=9 file=demo#9 assignee=codex stage=changes_requested" in captured.out
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
    tmp_path: Path,
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
        tmp_path / "docs" / "issues",
        9,
        summary="Looks good.",
        verification="uv run pytest",
        config=config,
    )
    completed = complete_issue(
        tmp_path / "docs" / "issues",
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
    tmp_path: Path,
    monkeypatch,
) -> None:
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
            )
        ]
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    config = IssuekitConfig(api_url="https://mine.example", project="demo")

    with pytest.raises(WorkflowError, match="self-review is not allowed") as excinfo:
        approve_issue(
            tmp_path / "docs" / "issues",
            9,
            verification="uv run pytest",
            reviewer="codex",
            config=config,
        )

    assert excinfo.value.code == "invalid_transition"


def test_submit_review_rejects_non_ascii_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["submit-review", "1", "--summary", "\u3042"])

    assert exit_code == 1
    assert "ASCII-only" in capsys.readouterr().err


def test_handoff_commands_reject_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    submit_exit = cli.main(
        ["submit-review", "bad-id", "--summary", "Implemented."],
    )
    request_exit = cli.main(["request-changes", "bad-id", "--notes", "Add tests."])

    assert submit_exit == 1
    assert request_exit == 1
    out = capsys.readouterr()
    assert "Invalid issue id: bad-id" in out.err
