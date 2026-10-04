"""Filter unsupported settings from machine configuration."""

from __future__ import annotations

import warnings
from dataclasses import fields
from pathlib import Path

from issuekit.agentrun.config import AgentRunConfig
from issuekit.core import VALID_ISSUE_PRIORITIES

from . import model
from .model import AgentPolicy, IssuekitConfig, RoleOverlay, RouterPolicy, TriagePolicy


def _discard_unsupported_machine_config(config: dict[str, object], path: Path) -> dict[str, object]:
    sanitized = {
        key: value
        for key, value in config.items()
        if _keep_machine_setting(path, str(key), str(key), _MACHINE_CONFIG_KEYS)
    }
    _discard_unsupported_table_keys(sanitized, "triage", _TRIAGE_CONFIG_KEYS, path)
    _discard_unsupported_table_keys(sanitized, "router", _ROUTER_CONFIG_KEYS, path)
    _discard_unsupported_agent_config(sanitized, path)
    _discard_unsupported_agent_roles(sanitized, path)
    _discard_invalid_machine_triage_priority(sanitized, path)
    return sanitized


_MACHINE_CONFIG_EXCLUDED_KEYS = frozenset(
    {
        # Machine worker identity belongs in issuekit.local.toml and is rejected above.
        "worker",
        # These are derived from [agents.<name>] instead of top-level TOML tables.
        "agent_policies",
        "agent_role_overlays",
        # These record config loading provenance rather than TOML settings.
        "machine_config_path",
        "repo_config_source",
        "api_url_source",
        "api_url_trusted_by",
    }
)

_MACHINE_CONFIG_KEYS = frozenset(
    config_field.name
    for config_field in fields(IssuekitConfig)
    if config_field.name not in _MACHINE_CONFIG_EXCLUDED_KEYS
)

_TRIAGE_CONFIG_KEYS = frozenset(config_field.name for config_field in fields(TriagePolicy))

_ROUTER_CONFIG_KEYS = frozenset(config_field.name for config_field in fields(RouterPolicy))

_AGENT_CONFIG_KEYS = (
    frozenset(
        config_field.name
        for config_field in fields(AgentRunConfig)
        if config_field.name != "approval_argv"
    )
    | frozenset(config_field.name for config_field in fields(AgentPolicy))
    | frozenset({"roles"})
)

_ROLE_OVERLAY_KEYS = frozenset(config_field.name for config_field in fields(RoleOverlay))

REPO_AGENT_KEYS = frozenset(
    {
        "model",
        "reasoning_effort",
        "speed",
        "roles",
        "prompt_suffix",
        "model_prompts",
        "mojibake_gate",
        "diff_shape_warn_deletions",
    }
)

_MACHINE_ONLY_AGENT_KEYS = _AGENT_CONFIG_KEYS - REPO_AGENT_KEYS


def _keep_machine_setting(
    path: Path,
    setting: str,
    candidate: str,
    supported: frozenset[str] | set[str],
) -> bool:
    if candidate in supported:
        return True
    warnings.warn(
        f"Ignoring unsupported machine config setting {setting} in {path}.",
        stacklevel=1,
    )
    return False


def _discard_unsupported_table_keys(
    config: dict[str, object], table_name: str, supported: frozenset[str], path: Path
) -> None:
    table = config.get(table_name)
    if not isinstance(table, dict):
        return
    config[table_name] = {
        key: value
        for key, value in table.items()
        if _keep_machine_setting(path, f"{table_name}.{key}", str(key), supported)
    }


def _discard_unsupported_agent_config(config: dict[str, object], path: Path) -> None:
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return
    for agent_name, agent_config in agents.items():
        if not isinstance(agent_config, dict):
            continue
        agents[agent_name] = {
            key: value
            for key, value in agent_config.items()
            if _keep_machine_setting(
                path, f"agents.{agent_name}.{key}", str(key), _AGENT_CONFIG_KEYS
            )
        }
        _discard_unsupported_role_overlays(agents[agent_name], str(agent_name), path)


def _discard_unsupported_role_overlays(
    agent_config: dict[str, object], agent_name: str, path: Path
) -> None:
    roles = agent_config.get("roles")
    if not isinstance(roles, dict):
        return
    supported_roles: dict[str, object] = {}
    for role, overlay in roles.items():
        if str(role) not in model.ROLE_OVERLAY_ROLES:
            _keep_machine_setting(
                path,
                f"agents.{agent_name}.roles.{role}",
                str(role),
                model.ROLE_OVERLAY_ROLES,
            )
            continue
        if isinstance(overlay, dict):
            supported_roles[role] = {
                key: value
                for key, value in overlay.items()
                if _keep_machine_setting(
                    path,
                    f"agents.{agent_name}.roles.{role}.{key}",
                    str(key),
                    _ROLE_OVERLAY_KEYS,
                )
            }
        else:
            supported_roles[role] = overlay
    agent_config["roles"] = supported_roles


def _discard_unsupported_agent_roles(config: dict[str, object], path: Path) -> None:
    agent_roles = config.get("agent_roles")
    if not isinstance(agent_roles, dict):
        return
    config["agent_roles"] = {
        agent: role
        for agent, role in agent_roles.items()
        if _keep_machine_setting(
            path, f"agent_roles.{agent} = {role}", str(role), model.AGENT_ROLES
        )
    }


def _discard_invalid_machine_triage_priority(config: dict[str, object], path: Path) -> None:
    triage = config.get("triage")
    if not isinstance(triage, dict):
        return
    priority = str(triage.get("default_priority", "")).strip()
    if priority and priority not in VALID_ISSUE_PRIORITIES:
        _keep_machine_setting(
            path,
            f"triage.default_priority = {priority}",
            priority,
            VALID_ISSUE_PRIORITIES,
        )
        del triage["default_priority"]
