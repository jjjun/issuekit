"""Custom agent adapter registry."""

from __future__ import annotations

from issuekit.agents.adapters.kimi import KimiAdapter
from issuekit.agents.runner import ConfigAgentAdapter


ADAPTERS: dict[str, type[ConfigAgentAdapter]] = {
    "kimi": KimiAdapter,
}


def resolve_custom_adapter(adapter: str) -> type[ConfigAgentAdapter] | None:
    """Return the custom adapter class for a config adapter marker."""
    return ADAPTERS.get(adapter)
