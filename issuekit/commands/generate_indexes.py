"""Compatibility handler for legacy generate-indexes instructions."""

from __future__ import annotations


def run(_args) -> int:
    print("generate-indexes is not used in API-backed mode; run `issuekit validate` instead.")
    return 0
