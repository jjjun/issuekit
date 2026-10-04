"""Shared helpers for reading and validating command input."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from issuekit.encoding import ASCII_ONLY_HINT, has_non_ascii


def read_text_file(path: str) -> str:
    """Read a prose file using the shared file-flag convention."""

    return Path(path).read_text(encoding="utf-8-sig").strip()


def resolve_text(
    inline: str | None,
    path: str | None,
    *,
    strip_inline: bool = True,
    prefer: Literal["inline", "file"] = "inline",
) -> str | None:
    if prefer == "file":
        if path is not None:
            return read_text_file(path)
        if inline is not None:
            return inline.strip() if strip_inline else inline
        return None

    if inline is not None:
        return inline.strip() if strip_inline else inline
    if path is not None:
        return read_text_file(path)
    return None


def require_ascii(
    *values: str,
    message: str,
    error: type[Exception] = ValueError,
) -> None:
    if any(has_non_ascii(value) for value in values):
        raise error(f"{message} {ASCII_ONLY_HINT}")


def active_issue_not_found(issue_id: int) -> str:
    return f"Active issue #{issue_id} was not found."
