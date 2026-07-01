from pathlib import Path

from issuekit import core
from issuekit import legacy_markdown
from issuekit.config import IssuekitConfig, load_config

from tests.issue_helpers import issue_text, write_issue


def test_read_issues_reads_optional_workflow_fields(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", assignee="codex", stage="implementing", implementer="codex"),
    )
    write_issue(issues_dir / "active" / "002_second.md", issue_text(2, "Second", author="claude"))

    issues = legacy_markdown.read_issues(issues_dir, "active")

    assert issues[0].assignee == "codex"
    assert issues[0].stage == "implementing"
    assert issues[0].implementer == "codex"
    assert issues[1].assignee == ""
    assert issues[1].stage == ""
    assert issues[1].implementer == ""
    assert issues[1].author == "claude"


def test_workflow_token_shape_rejects_frontmatter_injection() -> None:
    assert core.is_valid_workflow_token("")
    assert core.is_valid_workflow_token("codex")
    assert not core.is_valid_workflow_token("codex\nstatus: completed")
    assert not core.is_valid_workflow_token("review:done")
    assert not core.is_valid_workflow_token("bad value")
    assert not core.is_valid_workflow_token("-codex")


def test_load_config_reads_workflow_sets(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nassignees = ['alice']\nstages = ['draft']\ndefault_reviewer = 'alice'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice",),
        stages=("draft",),
        default_reviewer="alice",
    )
