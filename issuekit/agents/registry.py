"""Issuekit-side resolution of configured agent adapters."""

from __future__ import annotations

from issuekit.agents.runner import AgentAdapter, build_adapter
from issuekit.config import IssuekitConfig


def resolve_adapter(
    agent_name: str,
    config: IssuekitConfig | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> AgentAdapter:
    """Resolve a configured agent into a runtime adapter."""

    config = config or IssuekitConfig()
    run_config = dict(config.agents).get(agent_name)
    if run_config is None:
        if agent_name in config.disabled_agents:
            raise ValueError(f"Agent disabled by config: {agent_name}")
        raise ValueError(f"Unknown agent: {agent_name}")
    return build_adapter(
        agent_name,
        run_config,
        model=model,
        reasoning_effort=reasoning_effort,
    )
