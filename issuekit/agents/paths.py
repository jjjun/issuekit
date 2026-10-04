"""Shared path checks for agent runtime files."""

from __future__ import annotations

from pathlib import Path


def is_agent_runtime_entry(path: Path) -> bool:
    return bool(path.parts and path.parts[0] == ".agent-runs")
