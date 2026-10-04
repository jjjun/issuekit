"""Bounded readers for the ends of agent run logs."""

from __future__ import annotations

from pathlib import Path

DEFAULT_TAIL_BYTES = 65_536
_LINE_BREAKS = (
    b"\n",
    b"\r",
    b"\v",
    b"\f",
    b"\x1c",
    b"\x1d",
    b"\x1e",
    b"\xc2\x85",
    b"\xe2\x80\xa8",
    b"\xe2\x80\xa9",
)


def read_tail(path: Path, *, max_bytes: int = DEFAULT_TAIL_BYTES) -> bytes:
    """Read at most ``max_bytes`` from the end of a file."""

    max_bytes = max(0, max_bytes)
    with path.open("rb") as log_file:
        log_file.seek(0, 2)
        size = log_file.tell()
        log_file.seek(max(0, size - max_bytes))
        return log_file.read()


def last_nonempty_line_of(path: Path) -> tuple[str, int] | None:
    """Return the last non-empty decoded line and the file's modification time."""

    try:
        size = path.stat().st_size
        if size == 0:
            return None
        window = min(size, DEFAULT_TAIL_BYTES)
        while True:
            data = read_tail(path, max_bytes=window)
            lines = _complete_lines(path, data)
            for line in reversed(lines):
                stripped = line.strip()
                if stripped:
                    return stripped, path.stat().st_mtime_ns
            if len(data) >= size or window >= size:
                return None
            window = min(size, window * 2)
    except OSError:
        return None


def tail_lines(path: Path, n: int) -> list[str]:
    """Return the last ``n`` decoded lines from a file."""

    if n <= 0:
        return []
    size = path.stat().st_size
    if size == 0:
        return []
    window = min(size, DEFAULT_TAIL_BYTES)
    while True:
        data = read_tail(path, max_bytes=window)
        lines = _complete_lines(path, data)
        if len(lines) >= n or len(data) >= size or window >= size:
            return lines[-n:]
        window = min(size, window * 2)


def _complete_lines(path: Path, data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    offset = path.stat().st_size - len(data)
    if offset > 0 and not _starts_at_line_boundary(path, offset, data):
        return lines[1:]
    return lines


def _starts_at_line_boundary(path: Path, offset: int, data: bytes) -> bool:
    prefix_start = max(0, offset - 3)
    with path.open("rb") as log_file:
        log_file.seek(prefix_start)
        prefix = log_file.read(offset - prefix_start)
    if prefix.endswith(b"\r") and data.startswith(b"\n"):
        return False
    return any(prefix.endswith(line_break) for line_break in _LINE_BREAKS)
