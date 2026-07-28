"""Shared heartbeat and staleness command warnings."""

from __future__ import annotations

import sys


def warn_if_staleness_not_wider(
    stale_after_sec: float,
    heartbeat_interval_sec: float,
) -> None:
    if stale_after_sec > heartbeat_interval_sec:
        return
    print(
        "Warning: staleness window "
        f"({stale_after_sec:g}s) is not wider than the worker heartbeat interval "
        f"({heartbeat_interval_sec:g}s); a healthy worker may appear stale between beats.",
        file=sys.stderr,
    )
