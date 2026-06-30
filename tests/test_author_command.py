from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.commands.author import _slugify
from issuekit.testing import FakeIssuekitClient


def _configure_api(tmp_path: Path, monkeypatch, client: FakeIssuekitClient) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_author_command_creates_issue_via_api(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient()
    body_file = tmp_path / "plan.md"
    body_file.write_text(
        "## Problem\n\nSomething is missing.\n\n## Test Plan\n\n- uv run pytest\n",
        encoding="utf-8",
        newline="\n",
    )
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Add Author Command",
            "--body-file",
            str(body_file),
            "--priority",
            "high",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "API validation passed" not in captured.out
    assert "Authored issue: demo#1" in captured.out
    assert client.calls[0] == {
        "method": "create_issue",
        "body": {
            "title": "Add Author Command",
            "body": "## Problem\n\nSomething is missing.\n\n## Test Plan\n\n- uv run pytest",
            "priority": "high",
            "author": "codex",
        },
    }


def test_author_command_can_assign_explicit_implementer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Assigned Handoff",
            "--body",
            "## Problem\n\nAssign this.\n\n## Test Plan\n\n- issuekit validate",
            "--agent",
            "claude",
            "--assign",
            "kimi",
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    assert client.get_issue(1)["author"] == "claude"
    assert client.get_issue(1)["assignee"] == "kimi"


def test_slugify_preserves_title_length_for_authored_issues() -> None:
    assert _slugify("Feature: " + ("A" * 80)) == "feature_" + ("a" * 80)
