"""Helpers for issue-level dependency references."""

from __future__ import annotations

from collections.abc import Sequence
import re


DEPENDENCY_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+#[A-Za-z0-9_.:-]+$")


def dependency_refs(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = _split_dependency_refs(value)
    else:
        raw_items = []
        for item in value:
            raw_items.extend(_split_dependency_refs(str(item)))
    return _dedupe_refs(raw_items)


def _split_dependency_refs(value: str) -> list[str]:
    refs: list[str] = []
    for raw in re.split(r"[\s,]+", value):
        ref = raw.strip().strip(".;")
        if not ref:
            continue
        if not DEPENDENCY_REF_PATTERN.match(ref):
            raise ValueError(
                f"Invalid dependency reference: {ref}. Expected project#issue-or-proposal."
            )
        refs.append(ref)
    return refs


def _dedupe_refs(refs: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return tuple(deduped)
