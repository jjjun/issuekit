"""Custom agent adapter registry."""

from __future__ import annotations

from issuekit.agentrun.adapters.kimi import KimiAdapter
from issuekit.agentrun.adapter import ConfigAgentAdapter


ADAPTERS: dict[str, type[ConfigAgentAdapter]] = {
    "kimi": KimiAdapter,
}


def resolve_custom_adapter(adapter: str) -> type[ConfigAgentAdapter] | None:
    """Return the custom adapter class for a config adapter marker."""
    return ADAPTERS.get(adapter)
