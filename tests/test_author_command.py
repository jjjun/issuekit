from pathlib import Path

from issuekit import cli
from issuekit.core import read_issues
from issuekit.commands.author import _slugify

from tests.issue_helpers import make_issue_tree


def test_author_command_creates_valid_open_active_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = make_issue_tree(tmp_path)
    body_file = tmp_path / "plan.md"
    body_file.write_text(
        "## Problem\n\nSomething is missing.\n\n"
        "## Proposed Solution\n\nAdd it.\n\n"
        "## Test Plan\n\n- uv run pytest\n",
        encoding="utf-8",
        newline="\n",
    )

    monkeypatch.chdir(tmp_path)
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
    assert "API validation passed" in captured.out
    assert "Authored issue: active/003_add_author_command.md" in captured.out

    issue_path = issues_dir / "active" / "003_add_author_command.md"
    content = issue_path.read_text(encoding="utf-8")
    issues = read_issues(issues_dir, "active")
    authored = next(issue for issue in issues if issue.id == 3)

    assert authored.title == "Add Author Command"
    assert authored.issue_status == "active"
    assert authored.priority == "high"
    assert authored.assignee == ""
    assert authored.stage == "todo"
    assert authored.implementer == ""
    assert authored.author == "codex"
    assert "author: codex" in content
    assert "stage: todo" in content
    assert "assignee:" not in content
    assert "implementer:" not in content
    assert "# Issue #3: Add Author Command" in content
    assert "Something is missing." in content
    assert "003_add_author_command.md" in (issues_dir / "indexes" / "active.md").read_text(
        encoding="utf-8"
    )


def test_author_command_can_assign_explicit_implementer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = make_issue_tree(tmp_path)

    monkeypatch.chdir(tmp_path)
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
    authored = next(issue for issue in read_issues(issues_dir, "active") if issue.id == 3)
    content = authored.file_path.read_text(encoding="utf-8")

    assert authored.author == "claude"
    assert authored.assignee == "kimi"
    assert authored.stage == "todo"
    assert authored.implementer == ""
    assert "author: claude" in content
    assert "assignee: kimi" in content
    assert "implementer:" not in content


def test_slugify_preserves_title_length_for_authored_issues() -> None:
    assert _slugify("Feature: " + ("A" * 80)) == "feature_" + ("a" * 80)
