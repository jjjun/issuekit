"""Issuekit-side resolution of configured agent adapters."""

from __future__ import annotations

from dataclasses import replace

from issuekit.agentrun.adapter import AgentAdapter, build_adapter
from issuekit.config import IssuekitConfig
from issuekit.config.model import BUILTIN_ROLE_LAUNCH_POLICIES, READ_ONLY_ROLES


def resolve_adapter(
    agent_name: str,
    config: IssuekitConfig | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    role: str | None = None,
) -> AgentAdapter:
    """Resolve a configured agent into a runtime adapter."""

    config = config or IssuekitConfig()
    if agent_name in config.disabled_agents:
        raise ValueError(f"Agent disabled by config: {agent_name}")
    run_config = dict(config.agents).get(agent_name)
    if run_config is None:
        raise ValueError(f"Unknown agent: {agent_name}")
    if role is not None and role != "implementer":
        run_config = replace(run_config, prompt_suffix=None)
    role_overlay = dict(dict(config.agent_role_overlays).get(agent_name, ())).get(role)
    if role_overlay is not None:
        run_config = replace(
            run_config,
            model=role_overlay.model or run_config.model,
            reasoning_effort=role_overlay.reasoning_effort or run_config.reasoning_effort,
        )
    if role in READ_ONLY_ROLES:
        approval_argv = (
            role_overlay.approval_argv
            if role_overlay is not None and role_overlay.approval_argv is not None
            else BUILTIN_ROLE_LAUNCH_POLICIES.get(agent_name, {}).get(role)
        )
        if approval_argv is None:
            raise ValueError(
                f"Agent '{agent_name}' has no read-only launch policy for role '{role}'; "
                f"configure [agents.{agent_name}.roles.{role}] approval_argv to opt in."
            )
        run_config = replace(run_config, approval_argv=approval_argv)
    elif role_overlay is not None and role_overlay.approval_argv is not None:
        run_config = replace(run_config, approval_argv=role_overlay.approval_argv)
    return build_adapter(
        agent_name,
        run_config,
        model=model,
        reasoning_effort=reasoning_effort,
    )
