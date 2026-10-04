"""Safety checks for the local agent run directory."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

from issuekit.agentrun.git import run_git
from issuekit.file_permissions import ensure_owner_only_directory, open_owner_only


def prepare_run_dir(repo: Path, run_dir: Path | None = None) -> Path:
    """Refuse unsafe run directories and create the configured directory."""

    configured_dir = run_dir or repo / ".agent-runs"
    _reject_symlinked_run_dir(configured_dir)

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


@contextmanager
def serve_lock(lock_path: Path) -> Iterator[None]:
    """Hold a process-safe lock for one serve process in this run directory."""

    _reject_symlinked_run_dir(lock_path.parent)
    ensure_owner_only_directory(lock_path.parent)
    fd = open_owner_only(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        try:
            if _is_windows():
                if msvcrt is None:
                    raise RuntimeError("Windows file locking is unavailable.")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                if fcntl is None:
                    raise RuntimeError("POSIX file locking is unavailable.")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
                errno.EWOULDBLOCK,
            }:
                raise
            existing_pid = _read_lock_pid(lock_path)
            display_pid = existing_pid if existing_pid is not None else "unknown"
            raise ServeLockError(
                "issuekit serve is already running for this checkout "
                f"(pid {display_pid})."
            ) from None

        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(fd)


class ServeLockError(RuntimeError):
    """Raised when another process holds the serve lock."""


def _is_windows() -> bool:
    return os.name == "nt"


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _reject_symlinked_run_dir(configured_dir: Path) -> None:
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


def _has_git_metadata(repo: Path) -> bool:
    for directory in (repo, *repo.parents):
        marker = directory / ".git"
        if marker.is_file():
            return True
        if marker.is_dir() and (marker / "HEAD").is_file():
            return True
    return False
