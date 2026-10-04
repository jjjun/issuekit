"""Shared validators for structured agent output fields."""

from __future__ import annotations

from typing import TextIO

from issuekit.encoding import has_non_ascii, sanitize_to_ascii


def require_str(value: object, *, error: type[Exception], message: str) -> str:
    if not isinstance(value, str):
        raise error(message)
    return value


def require_nonempty_text(
    value: object,
    *,
    error: type[Exception],
    message: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error(message)
    return value.strip()


def sanitize_ascii_field(
    key: str,
    value: str,
    *,
    err: TextIO,
    actor: str,
    recording: str,
) -> str:
    if not has_non_ascii(value):
        return value
    sanitized = sanitize_to_ascii(value).strip()
    marker = f"[{key} sanitized from non-ASCII]"
    print(
        f"WARNING: {actor} agent field {key} contained non-ASCII text; "
        f"sanitized before recording {recording}.",
        file=err,
    )
    if not sanitized:
        return marker
    return f"{sanitized}\n\n{marker}"
