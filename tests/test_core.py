from pathlib import Path

import pytest

from issuekit import core, legacy_markdown
from issuekit.config import IssuekitConfig, load_config


def write_issue(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_parse_frontmatter_strips_bom_and_quotes() -> None:
    parsed = legacy_markdown.parse_issue_frontmatter(
        '\ufeff---\nid: 7\ntitle: "Quoted title"\ncompleted:\n---\n\n# Body\n'
    )

    assert parsed.has_frontmatter
    assert parsed.data["id"] == "7"
    assert parsed.data["title"] == "Quoted title"
    assert parsed.data["completed"] == ""
    assert parsed.body == "\n# Body\n"


def test_read_issues_and_next_id(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        "---\nid: 1\nstatus: active\npriority: high\ncreated: 2026-01-01\ncompleted:\ntitle: First\n---\n\n# Issue #1: First\n",
    )
    write_issue(issues_dir / "completed" / "002_second.md", "# Issue #2: Second\n")

    active, completed, all_issues = legacy_markdown.read_all_issues(issues_dir)

    assert [issue.title for issue in active] == ["First"]
    assert [issue.title for issue in completed] == ["Second"]
    assert [issue.id for issue in all_issues] == [1, 2]


def test_read_issues_returns_decode_error_issue_for_non_utf8_file(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    bad_issue = issues_dir / "active" / "003_cp932.md"
    bad_issue.parent.mkdir(parents=True, exist_ok=True)
    bad_issue.write_bytes(b"# Issue #3: \x83e\x83X\x83g\n")

    issues = legacy_markdown.read_issues(issues_dir, "active")

    assert len(issues) == 1
    assert issues[0].id == 3
    assert issues[0].file_name_id == 3
    assert issues[0].title == "cp932"
    assert issues[0].relative_path == "active/003_cp932.md"
    assert issues[0].content == ""
    assert issues[0].frontmatter == legacy_markdown.Frontmatter(
        data={},
        body="",
        has_frontmatter=False,
    )
    assert issues[0].decode_error is True


def test_mojibake_detection() -> None:
    assert core.has_mojibake("\u7e67")
    assert core.has_mojibake("\ufffd")
    assert not core.has_mojibake("plain ascii")


def test_valid_issue_statuses_match_server_derived_labels() -> None:
    assert core.VALID_ISSUE_STATUSES == {
        "active",
        "planned",
        "in_progress",
        "completed",
    }


def test_load_config_reads_tool_issuekit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nascii_id_threshold = 100\nissues_dir = 'custom/issues'\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config == IssuekitConfig(
        ascii_id_threshold=100,
        issues_dir="custom/issues",
        assignees=IssuekitConfig.assignees,
        stages=IssuekitConfig.stages,
    )
    assert config.issues_path(tmp_path) == tmp_path / "custom" / "issues"


def test_parse_issue_id_arg() -> None:
    assert core.parse_issue_id_arg("42") == 42
    with pytest.raises(ValueError, match="Invalid issue id: not-a-number"):
        core.parse_issue_id_arg("not-a-number")
