"""Implementation of the generate-indexes command."""

from __future__ import annotations

from pathlib import Path

from issuekit.config import load_config
from issuekit.core import build_index_files, read_all_issues


def run(_args) -> int:
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    write_index_files(issues_dir, config.recent_count)
    return 0


def write_index_files(issues_dir: Path, recent_count: int) -> dict[str, str]:
    indexes_dir = issues_dir / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    for path in indexes_dir.iterdir():
        if path.suffix == ".md":
            path.unlink()

    active_issues, completed_issues, _ = read_all_issues(issues_dir)
    index_files = build_index_files(active_issues, completed_issues, recent_count)
    for name, content in index_files.items():
        (indexes_dir / name).write_text(content, encoding="utf-8", newline="\n")
    return index_files
