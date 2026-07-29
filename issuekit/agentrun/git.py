"""Minimal git helpers used while supervising agent runs."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_status_short(cwd: Path, *, timeout: float = 30) -> str | None:
    """Return stripped ``git status --short`` output, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "--no-pager", "status", "--short"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def changed_file_count(cwd: Path, *, timeout: float = 5) -> int:
    """Return the number of changed files from ``git status --short``."""
    status = git_status_short(cwd, timeout=timeout)
    if status is None:
        return 0
    return sum(1 for line in status.splitlines() if line.strip())
