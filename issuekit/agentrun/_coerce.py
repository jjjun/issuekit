"""Small runtime-local text and value helpers."""

from __future__ import annotations


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def last_nonempty_line(text: str) -> str | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None
