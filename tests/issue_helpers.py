from pathlib import Path

from issuekit.core import build_index_files, read_all_issues


def write_issue(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def issue_text(
    issue_id: int,
    title: str,
    *,
    status: str = "active",
    priority: str = "medium",
    created: str = "2026-01-01",
    completed: str = "",
    assignee: str = "",
    stage: str = "",
) -> str:
    workflow_lines = ""
    if assignee:
        workflow_lines += f"assignee: {assignee}\n"
    if stage:
        workflow_lines += f"stage: {stage}\n"
    return (
        "---\n"
        f"id: {issue_id}\n"
        f"status: {status}\n"
        f"priority: {priority}\n"
        f"created: {created}\n"
        f"completed: {completed}\n"
        f"{workflow_lines}"
        f"title: {title}\n"
        "---\n\n"
        f"# Issue #{issue_id}: {title}\n"
    )


def make_issue_tree(tmp_path: Path) -> Path:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First", priority="high"))
    write_issue(
        issues_dir / "completed" / "002_done.md",
        issue_text(2, "Done", status="completed", priority="low", completed="2026-01-02"),
    )
    write_indexes(issues_dir)
    return issues_dir


def write_indexes(issues_dir: Path, recent_count: int = 30) -> None:
    active, completed, _ = read_all_issues(issues_dir)
    indexes = build_index_files(active, completed, recent_count)
    indexes_dir = issues_dir / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    for name, content in indexes.items():
        (indexes_dir / name).write_text(content, encoding="utf-8", newline="\n")
