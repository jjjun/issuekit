"""Helpers for the string-valued parsed agent result envelope."""

from __future__ import annotations

from collections.abc import Mapping


def int_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        name: count
        for name, count in value.items()
        if isinstance(name, str) and isinstance(count, int) and not isinstance(count, bool)
    }


def encode_usage(counts: Mapping[str, int]) -> dict[str, str]:
    return {f"usage_{name}": str(count) for name, count in counts.items()}


def parsed_usage(parsed: Mapping[str, str] | None) -> dict[str, int]:
    usage: dict[str, int] = {}
    for key, value in (parsed or {}).items():
        if not key.startswith("usage_"):
            continue
        try:
            usage[key.removeprefix("usage_")] = int(value)
        except ValueError:
            continue
    return usage


def parsed_is_error(parsed: Mapping[str, str] | None) -> bool | None:
    value = (parsed or {}).get("is_error")
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def parsed_int(parsed: Mapping[str, str] | None, key: str) -> int | None:
    value = (parsed or {}).get(key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
