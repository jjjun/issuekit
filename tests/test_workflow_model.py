from pathlib import Path

from issuekit import cli, core
from issuekit.config import IssuekitConfig, load_config

from tests.issue_helpers import issue_text, write_indexes, write_issue


def test_read_issues_reads_optional_workflow_fields(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", assignee="codex", stage="implementing", implementer="codex"),
    )
    write_issue(issues_dir / "active" / "002_second.md", issue_text(2, "Second"))

    issues = core.read_issues(issues_dir, "active")

    assert issues[0].assignee == "codex"
    assert issues[0].stage == "implementing"
    assert issues[0].implementer == "codex"
    assert issues[1].assignee == ""
    assert issues[1].stage == ""
    assert issues[1].implementer == ""


def test_format_issue_frontmatter_omits_empty_workflow_fields() -> None:
    data = {
        "id": 1,
        "status": "active",
        "priority": "high",
        "created": "2026-01-01",
        "completed": "",
        "title": "First",
    }

    assert core.format_issue_frontmatter(data) == (
        "---\n"
        "id: 1\n"
        "status: active\n"
        "priority: high\n"
        "created: 2026-01-01\n"
        "completed: \n"
        "title: First\n"
        "---\n\n"
    )


def test_format_issue_frontmatter_orders_workflow_fields() -> None:
    data = {
        "id": 1,
        "status": "in_progress",
        "priority": "high",
        "created": "2026-01-01",
        "completed": "",
        "assignee": "codex",
        "stage": "implementing",
        "implementer": "codex",
        "title": "First",
    }

    assert core.format_issue_frontmatter(data).splitlines()[:10] == [
        "---",
        "id: 1",
        "status: in_progress",
        "priority: high",
        "created: 2026-01-01",
        "completed: ",
        "assignee: codex",
        "stage: implementing",
        "implementer: codex",
        "title: First",
    ]


def test_workflow_token_shape_rejects_frontmatter_injection() -> None:
    assert core.is_valid_workflow_token("")
    assert core.is_valid_workflow_token("codex")
    assert not core.is_valid_workflow_token("codex\nstatus: completed")
    assert not core.is_valid_workflow_token("review:done")
    assert not core.is_valid_workflow_token("bad value")
    assert not core.is_valid_workflow_token("-codex")


def test_validate_rejects_unknown_workflow_values(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_bad.md",
        issue_text(1, "Bad", assignee="bob", stage="foo", implementer="bob"),
    )
    write_issue(
        issues_dir / "active" / "002_bad_implementer.md",
        issue_text(2, "Bad Implementer", implementer="bad value"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "unknown assignee" in err
    assert "invalid implementer token" in err
    assert "unknown implementer" in err
    assert "unknown stage" in err


def test_validate_allows_configured_workflow_values(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nassignees = ['alice']\nstages = ['draft']\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_ok.md",
        issue_text(1, "Ok", assignee="alice", stage="draft"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    assert exit_code == 0
    assert "Issue validation passed" in capsys.readouterr().out


def test_load_config_reads_workflow_sets(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nassignees = ['alice']\nstages = ['draft']\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(assignees=("alice",), stages=("draft",))


def test_write_issue_atomic_writes_utf8_lf_without_bom(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "issues" / "active" / "001_first.md"
    core.write_issue_atomic(path, "line 1\r\nline 2\r\n")
    core.write_issue_atomic(path, "next\r\n")

    content = path.read_bytes()
    assert content == b"next\n"
    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in content
