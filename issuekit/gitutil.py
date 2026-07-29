"""Shared helpers for git subprocess calls."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitResult:
    """Normalized git subprocess result."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitStatusEntry:
    """One NUL-delimited porcelain status record."""

    status: str
    path: Path
    original_path: Path | None = None


def run_git(
    args: Sequence[str],
    cwd: Path | str,
    *,
    timeout: float = 30,
    strict: bool = False,
) -> GitResult | None:
    """Run git with consistent stdio, timeout, and error handling.

    Return None for execution failures unless strict is true.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        if strict:
            raise
        return None
    return GitResult(
        returncode=result.returncode,
        stdout=_decode_stream(result.stdout),
        stderr=_decode_stream(result.stderr),
    )


def _decode_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return bytes(value).decode("utf-8", errors="replace")


def git_status_short(
    cwd: Path | str,
    *,
    strip: bool = True,
    untracked_files: str | None = None,
    timeout: float = 30,
) -> str | None:
    """Return `git status --short` output, stripped, or None on failure."""
    args = ["-c", "core.quotepath=false", "--no-pager", "status", "--short"]
    if untracked_files is not None:
        args.append(f"--untracked-files={untracked_files}")
    result = run_git(args, cwd, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() if strip else result.stdout


def parse_git_status_z(output: str) -> tuple[GitStatusEntry, ...]:
    """Parse ``git status --porcelain=v1 -z`` output."""

    records = output.split("\0")
    if records and not records[-1]:
        records.pop()
    entries: list[GitStatusEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4 or record[2] != " ":
            raise ValueError("Malformed NUL-delimited Git status record.")
        status = record[:2]
        path = Path(record[3:])
        original_path = None
        if "R" in status or "C" in status:
            index += 1
            if index >= len(records) or not records[index]:
                raise ValueError("Git rename or copy status is missing its original path.")
            original_path = Path(records[index])
        entries.append(
            GitStatusEntry(
                status=status,
                path=path,
                original_path=original_path,
            )
        )
        index += 1
    return tuple(entries)


def git_status_entries(
    cwd: Path | str,
    *,
    untracked_files: str = "all",
    timeout: float = 30,
) -> tuple[GitStatusEntry, ...] | None:
    """Return authoritative porcelain status entries, or None on failure."""

    result = run_git(
        [
            "--no-pager",
            "status",
            "--porcelain=v1",
            "-z",
            f"--untracked-files={untracked_files}",
        ],
        cwd,
        timeout=timeout,
    )
    if result is None or result.returncode != 0:
        return None
    try:
        return parse_git_status_z(result.stdout)
    except ValueError:
        return None


def git_root(cwd: Path | str, *, timeout: float = 30) -> Path | None:
    """Return the repository root for cwd, or None when cwd is not in a repo."""
    result = run_git(["rev-parse", "--show-toplevel"], cwd, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None


def git_short_head(cwd: Path | str, *, timeout: float = 5) -> str | None:
    """Return the short HEAD commit hash, or None on failure."""
    result = run_git(["rev-parse", "--short", "HEAD"], cwd, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_current_branch(cwd: Path | str, *, timeout: float = 5) -> str | None:
    """Return the current branch name, or None outside a branch checkout."""
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def git_origin_url(cwd: Path | str, *, timeout: float = 5) -> str | None:
    """Return remote.origin.url, or None when it is unavailable."""
    result = run_git(["config", "--get", "remote.origin.url"], cwd, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def changed_file_count(cwd: Path | str, *, timeout: float = 5) -> int:
    """Return the count of changed files from `git status --short`."""
    status = git_status_short(cwd, timeout=timeout)
    if status is None:
        return 0
    return sum(1 for line in status.splitlines() if line.strip())
