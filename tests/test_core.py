from pathlib import Path

from issuekit import core
from issuekit.config import IssuekitConfig, load_config


def write_issue(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_parse_frontmatter_strips_bom_and_quotes() -> None:
    parsed = core.parse_issue_frontmatter(
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

    active, completed, all_issues = core.read_all_issues(issues_dir)

    assert [issue.title for issue in active] == ["First"]
    assert [issue.title for issue in completed] == ["Second"]
    assert core.get_next_issue_id(all_issues) == 3
    assert core.group_issues_by_id(all_issues)[1][0].relative_path == "active/001_first.md"


def test_build_index_files(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        "---\nid: 1\nstatus: active\npriority: high\ncreated: 2026-01-01\ncompleted:\ntitle: First\n---\n\n# Issue #1: First\n",
    )
    write_issue(
        issues_dir / "completed" / "102_done.md",
        "---\nid: 102\nstatus: completed\npriority: low\ncreated: 2026-01-01\ncompleted: 2026-01-02\ntitle: Done\n---\n\n# Issue #102: Done\n",
    )
    active, completed, _ = core.read_all_issues(issues_dir)

    files = core.build_index_files(active, completed, recent_count=1)

    assert set(files) == {
        "active.md",
        "completed-recent.md",
        "completed-001-099.md",
        "completed-100-199.md",
    }
    assert core.GENERATED_FILE_MARKER in files["active.md"]
    assert "| 1 | First | high | active | [active/001_first.md](../active/001_first.md) |" in files[
        "active.md"
    ]
    assert "| 102 | Done | 2026-01-02 | [completed/102_done.md](../completed/102_done.md) |" in files[
        "completed-100-199.md"
    ]


def test_mojibake_detection() -> None:
    assert core.has_mojibake("郢ｧ")
    assert not core.has_mojibake("plain ascii")


def test_load_config_reads_tool_issuekit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nrecent_count = 5\nascii_id_threshold = 100\nissues_dir = 'custom/issues'\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config == IssuekitConfig(
        recent_count=5,
        ascii_id_threshold=100,
        issues_dir="custom/issues",
    )
    assert config.issues_path(tmp_path) == tmp_path / "custom" / "issues"
