"""Best-effort owner-only permissions for locally sensitive files."""

from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path


def ensure_owner_only_directory(path: Path) -> None:
    """Create a directory and restrict it to its owner where supported."""

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    chmod_700(path)


def open_owner_only(
    path: Path,
    flags: int,
    *,
    warn: Callable[[Path, str], None] | None = None,
    windows_acl: bool = False,
) -> int:
    """Open a file with owner-only permissions where supported."""

    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    chmod_600(path, warn=warn, windows_acl=windows_acl)
    return fd


def open_owner_only_new(path: Path, *, append: bool = False) -> int:
    """Open a new owner-only file, or safely open one for owner-only append."""

    flags = os.O_WRONLY | os.O_CREAT
    if append:
        flags |= os.O_APPEND
    else:
        flags |= os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if os.name != "nt":
            if os.fstat(fd).st_uid != os.getuid():
                raise PermissionError(f"Refusing file not owned by current user: {path}")
            os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_owner_only_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file without following a final symlink."""

    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp_path = path.with_name(os.path.basename(temp_name))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def chmod_600(
    path: Path,
    *,
    warn: Callable[[Path, str], None] | None = None,
    windows_acl: bool = False,
    strict: bool = False,
) -> None:
    """Best-effort restriction of a file to its owner."""

    if os.name == "nt":
        if not windows_acl:
            return
        reason = _restrict_windows_acl(path)
        if reason and warn is not None:
            warn(path, reason)
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        if strict:
            raise


def chmod_700(path: Path) -> None:
    """Best-effort restriction of a directory to its owner."""

    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _restrict_windows_acl(path: Path) -> str | None:
    grantee = _current_windows_acl_grantee()
    if not grantee:
        return "current Windows user could not be determined"
    try:
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{grantee}:F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return result.stderr.strip() or f"icacls exited with {result.returncode}"
    return None


def _current_windows_acl_grantee() -> str | None:
    sid = _current_windows_user_sid()
    if sid:
        return f"*{sid}"
    try:
        login = os.getlogin()
    except OSError:
        login = ""
    if login:
        return login
    username = os.getenv("USERNAME")
    if not username:
        return None
    domain = os.getenv("USERDOMAIN")
    if domain:
        return f"{domain}\\{username}"
    return username


def _current_windows_user_sid() -> str | None:
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv"],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        rows = list(csv.DictReader(io.StringIO(result.stdout or "")))
    except csv.Error:
        return None
    if not rows:
        return None
    sid = (rows[0].get("SID") or "").strip()
    return sid if sid.startswith("S-1-") else None
