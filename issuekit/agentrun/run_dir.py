"""Safety checks for the local agent run directory."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from issuekit.agentrun.git import run_git
from issuekit.file_permissions import ensure_owner_only_directory


def prepare_run_dir(repo: Path, run_dir: Path | None = None) -> Path:
    """Refuse unsafe run directories and create the configured directory."""

    configured_dir = run_dir or repo / ".agent-runs"
    try:
        mode = configured_dir.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and stat.S_ISLNK(mode):
        target = os.readlink(configured_dir)
        raise RuntimeError(
            "Refusing .agent-runs: it is a symlink to "
            f"{target}. Remove it; issuekit run logs must live inside the checkout."
        )

    if _has_git_metadata(repo):
        with tempfile.TemporaryDirectory(prefix="issuekit-no-git-hooks-") as hooks_path:
            result = run_git(
                [
                    "-c",
                    f"core.hooksPath={hooks_path}",
                    "ls-files",
                    "-z",
                    "--",
                    ".agent-runs",
                ],
                repo,
            )
        if result.returncode != 0:
            raise RuntimeError("Could not check whether git tracks files under .agent-runs.")
        tracked_output = result.stdout.encode("utf-8", errors="surrogateescape")
        tracked = [path for path in tracked_output.split(b"\0") if path]
        if tracked:
            first_path = os.fsdecode(tracked[0])
            raise RuntimeError(
                f"Refusing .agent-runs: git tracks {len(tracked)} files under it "
                f"({first_path}). Remove them from the branch."
            )

    prepared_dir = configured_dir.absolute()
    ensure_owner_only_directory(prepared_dir)
    return prepared_dir


def _has_git_metadata(repo: Path) -> bool:
    for directory in (repo, *repo.parents):
        marker = directory / ".git"
        if marker.is_file():
            return True
        if marker.is_dir() and (marker / "HEAD").is_file():
            return True
    return False
