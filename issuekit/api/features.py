"""API feature compatibility helpers."""

from __future__ import annotations


def is_feature_unavailable(exc: BaseException) -> bool:
    return getattr(exc, "code", None) in {"not_found", "http_404"}
