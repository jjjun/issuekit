"""Implementation of the generate-indexes command."""

from __future__ import annotations

from pathlib import Path

from issuekit.config import load_config
from issuekit.core import build_index_files, read_active_issues, read_completed_issues


def run(_args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    write_index_files(issues_dir, config.recent_count)
    return 0


def write_index_files(issues_dir: Path, recent_count: int) -> dict[str, str]:
    indexes_dir = issues_dir / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)

    active_issues = read_active_issues(issues_dir)
    completed_issues = read_completed_issues(issues_dir)
    index_files = build_index_files(active_issues, completed_issues, recent_count)
    for name, content in index_files.items():
        path = indexes_dir / name
        if path.exists() and path.read_text(encoding="utf-8-sig") == content:
            continue
        path.write_text(content, encoding="utf-8", newline="\n")

    for path in indexes_dir.iterdir():
        if path.suffix != ".md":
            continue
        if path.name not in index_files:
            path.unlink()

    return index_files
