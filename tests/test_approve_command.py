from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


def _configure_api(tmp_path: Path, monkeypatch, client: FakeIssuekitClient) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_approve_completes_review_stage_issue(tmp_path: Path, monkeypatch, capsys) -> None:
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
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["approve", "1", "--verification", "uv run pytest"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Approved issue #1: demo#1" in captured.out
    assert "API validation passed" not in captured.out
    assert client.get_issue(1)["status"] == "completed"
    assert client.calls[0] == {
        "method": "approve",
        "number": 1,
        "body": {
            "summary": "Approved.",
            "verification": "uv run pytest",
            "reviewer": "claude",
        },
    }


def test_approve_warns_about_uncommitted_changes(tmp_path: Path, monkeypatch, capsys) -> None:
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
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.commands.approve.git_status_short", lambda cwd: " M file.py")

    assert cli.main(["approve", "1", "--verification", "uv run pytest"]) == 0

    assert "approval is being recorded with uncommitted changes" in capsys.readouterr().err


def test_approve_rejects_non_review_stage_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Anchor", stage="todo")])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["approve", "1", "--verification", "no local code scope"])

    assert exit_code == 1
    assert "not at the review stage" in capsys.readouterr().err
    assert client.get_issue(1)["status"] == "active"
    assert client.calls == [
        {
            "method": "approve",
            "number": 1,
            "body": {
                "summary": "Approved.",
                "verification": "no local code scope",
                "reviewer": "codex",
            },
        },
    ]


def test_approve_accepts_explicit_summary_and_reviewer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="review",
                implementer="claude",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "approve",
            "1",
            "--verification",
            "uv run pytest",
            "--summary",
            "Reviewed and approved.",
            "--reviewer",
            "codex",
        ]
    )

    assert exit_code == 0
    assert "Approved issue #1" in capsys.readouterr().out
    assert client.calls[0]["body"]["summary"] == "Reviewed and approved."
    assert client.calls[0]["body"]["reviewer"] == "codex"


def test_approve_respects_self_review_guard(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="review",
                implementer="codex",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        ["approve", "1", "--verification", "uv run pytest", "--reviewer", "codex"]
    )

    assert exit_code == 1
    assert "self-review is not allowed" in capsys.readouterr().err
    assert client.get_issue(1)["status"] == "in_progress"


def test_approve_rejects_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["approve", "bad-id", "--verification", "pytest"])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err
