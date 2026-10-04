"""Agent configuration parsing and validation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PureWindowsPath
from string import Formatter
from typing import TypeVar

from issuekit.agentrun.config import AgentRunConfig
from issuekit.coerce import optional_int, optional_str
from issuekit.core import is_valid_workflow_token

from . import model
from .model import AGENT_ROLES, AgentPolicy, IssuekitConfig, RoleOverlay
from .values import _bool_value, _dedupe_tokens, _string_tuple

_SENTINEL = object()
_T = TypeVar("_T")


def _load_disabled_agents(value: object) -> tuple[str, ...]:
    disabled_agents = _dedupe_tokens(_string_tuple(value))
    for agent in disabled_agents:
        if not agent or not is_valid_workflow_token(agent):
            raise ValueError(f"Invalid disabled_agents token: {agent}")
    return disabled_agents


def _load_agent_roles(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("agent_roles must be a table.")
    agent_roles: dict[str, str] = {}
    for raw_agent, raw_role in value.items():
        agent = str(raw_agent).strip()
        if not agent or not is_valid_workflow_token(agent):
            raise ValueError(f"Invalid agent_roles token: {raw_agent}")
        role = str(raw_role).strip()
        if role not in AGENT_ROLES:
            raise ValueError(f"Invalid agent_roles role: {role}")
        agent_roles[agent] = role
    return agent_roles


def _load_assignees(
    raw_config: dict[str, object],
    agents: tuple[tuple[str, AgentRunConfig], ...],
    disabled_agents: tuple[str, ...],
) -> tuple[str, ...]:
    if "assignees" in raw_config:
        assignees = _string_tuple(raw_config["assignees"])
    else:
        enabled_agent_names = tuple(name for name, _run_config in agents)
        builtin_order = tuple(
            name for name in IssuekitConfig.assignees if name in enabled_agent_names
        )
        extra_agents = tuple(
            name for name in enabled_agent_names if name not in IssuekitConfig.assignees
        )
        assignees = builtin_order + extra_agents
    disabled = set(disabled_agents)
    return tuple(assignee for assignee in assignees if assignee not in disabled)


def _validate_not_disabled(
    field: str,
    value: str,
    disabled_agents: tuple[str, ...],
) -> None:
    if value and value in set(disabled_agents):
        raise ValueError(f"{field} references disabled agent: {value}")


def _validate_default_implementer(default_implementer: str, assignees: tuple[str, ...]) -> None:
    if not default_implementer:
        return
    if not is_valid_workflow_token(default_implementer):
        raise ValueError(f"Invalid default_implementer token: {default_implementer}")
    if default_implementer not in assignees:
        raise ValueError(f"Unknown default_implementer: {default_implementer}")


def _load_agents(
    raw: dict[str, object],
) -> tuple[
    tuple[tuple[str, AgentRunConfig], ...],
    tuple[tuple[str, AgentPolicy], ...],
    tuple[tuple[str, tuple[tuple[str, RoleOverlay], ...]], ...],
]:
    if not raw:
        return (
            IssuekitConfig.agents,
            IssuekitConfig.agent_policies,
            IssuekitConfig.agent_role_overlays,
        )
    default_agents = IssuekitConfig.agents
    default_by_name = dict(default_agents)
    default_policies = dict(IssuekitConfig.agent_policies)
    configured: dict[str, AgentRunConfig] = {}
    configured_policies: dict[str, AgentPolicy] = {}
    configured_role_overlays: dict[str, tuple[tuple[str, RoleOverlay], ...]] = {}
    new_agent_names: list[str] = []
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        base = default_by_name.get(name, AgentRunConfig(binary=name))
        configured[name] = replace(base, **_agent_run_config_overrides(cfg))
        if configured[name].runtime == "codex_app_server" and name != "codex":
            raise ValueError("codex_app_server runtime is supported only for agents.codex.")
        policy = default_policies.get(name, AgentPolicy())
        configured_policies[name] = replace(policy, **_agent_policy_overrides(cfg))
        configured_role_overlays[name] = _agent_role_overlays(cfg, agent_name=name)
        if name not in default_by_name:
            new_agent_names.append(name)

    result: list[tuple[str, AgentRunConfig]] = []
    for name, default_config in default_agents:
        result.append((name, configured.get(name, default_config)))
    for name in new_agent_names:
        result.append((name, configured[name]))
    policies = tuple(
        (name, configured_policies.get(name, default_policies.get(name, AgentPolicy())))
        for name, _run_config in result
    )
    role_overlays = tuple(
        (name, configured_role_overlays[name])
        for name, _run_config in result
        if name in configured_role_overlays and configured_role_overlays[name]
    )
    return tuple(result), policies, role_overlays


def _agent_run_config_overrides(cfg: dict[str, object]) -> dict[str, object]:
    loaders = {
        "binary": str,
        "adapter": optional_str,
        "runtime": str,
        "app_server_argv": _string_tuple,
        "lease_ttl_seconds": int,
        "known_paths": _string_tuple,
        "headless_argv": _string_tuple,
        "resumable": _bool_value,
        "session_flag": optional_str,
        "resume_flag": optional_str,
        "approval_flag": optional_str,
        "approval_value": optional_str,
        "output_format_flag": optional_str,
        "output_format": optional_str,
        "model_flag": optional_str,
        "model": optional_str,
        "reasoning_effort": optional_str,
        "effort_argv": _string_tuple,
        "speed": _bool_value,
        "speed_argv": _string_tuple,
        "prompt_suffix": optional_str,
        "model_prompts": _model_prompts,
    }
    overrides: dict[str, object] = {}
    for key, loader in loaders.items():
        value = cfg.get(key, _SENTINEL)
        if value is not _SENTINEL:
            overrides[key] = loader(value)
    binary = overrides.get("binary")
    if binary is not None:
        overrides["binary"] = _validate_agent_command_or_path(binary, "binary")
    known_paths = overrides.get("known_paths")
    if known_paths is not None:
        overrides["known_paths"] = tuple(
            _validate_agent_command_or_path(path, "known_paths") for path in known_paths
        )
    effort_argv = overrides.get("effort_argv")
    if effort_argv is not None:
        _validate_effort_argv(effort_argv)
    runtime = overrides.get("runtime")
    if runtime is not None and runtime not in {"exec", "codex_app_server"}:
        raise ValueError("Agent runtime must be 'exec' or 'codex_app_server'.")
    lease_ttl = overrides.get("lease_ttl_seconds")
    if lease_ttl is not None and not 15 <= lease_ttl <= 300:
        raise ValueError("lease_ttl_seconds must be between 15 and 300.")
    app_server_argv = overrides.get("app_server_argv")
    if app_server_argv is not None:
        _validate_app_server_argv(app_server_argv)
    return overrides


def _validate_agent_command_or_path(value: object, setting: str) -> str:
    text = str(value)
    if not text:
        raise ValueError(f"{setting} must be a bare command name or an absolute path.")
    expanded = Path(text).expanduser()
    has_separator = "/" in text or "\\" in text
    is_absolute = expanded.is_absolute() or PureWindowsPath(text).is_absolute()
    if has_separator and not is_absolute:
        raise ValueError(f"{setting} must be a bare command name or an absolute path.")
    if has_separator and expanded.is_absolute():
        return str(expanded)
    return text


def _validate_effort_argv(value: object) -> None:
    for entry in value:
        try:
            fields_in_entry = tuple(Formatter().parse(entry))
        except ValueError as exc:
            raise ValueError(f"Invalid effort_argv template: {exc}") from exc
        for _literal, field_name, format_spec, conversion in fields_in_entry:
            if field_name is not None and (field_name != "value" or format_spec or conversion):
                raise ValueError(
                    "Invalid effort_argv template: only the {value} placeholder is allowed."
                )


def _validate_app_server_argv(value: object) -> None:
    argv = tuple(str(item) for item in value)
    if not argv or argv[0] != "app-server":
        raise ValueError("app_server_argv must launch 'app-server'.")
    for index, entry in enumerate(argv):
        if entry == "--listen":
            listener = argv[index + 1] if index + 1 < len(argv) else ""
            if listener != "stdio://":
                raise ValueError("app_server_argv may listen only on stdio://.")
        elif entry.startswith("--listen=") and entry != "--listen=stdio://":
            raise ValueError("app_server_argv may listen only on stdio://.")


def _agent_policy_overrides(cfg: dict[str, object]) -> dict[str, object]:
    loaders = {
        "mojibake_gate": _bool_value,
        "diff_shape_warn_deletions": optional_int,
    }
    return {
        key: loader(value)
        for key, loader in loaders.items()
        if (value := cfg.get(key, _SENTINEL)) is not _SENTINEL
    }


def _agent_role_overlays(
    cfg: dict[str, object], *, agent_name: str
) -> tuple[tuple[str, RoleOverlay], ...]:
    raw_roles = cfg.get("roles")
    if raw_roles is None:
        return ()
    if not isinstance(raw_roles, dict):
        raise ValueError(f"agents.{agent_name}.roles must be a table.")
    overlays: list[tuple[str, RoleOverlay]] = []
    for raw_role, raw_overlay in raw_roles.items():
        role = str(raw_role).strip()
        if role not in model.ROLE_OVERLAY_ROLES:
            raise ValueError(
                f"Invalid agents.{agent_name}.roles role: {role}; supported roles: "
                "implementer, reviewer, router, triage, negotiation."
            )
        if not isinstance(raw_overlay, dict):
            raise ValueError(f"agents.{agent_name}.roles.{role} must be a table.")
        unexpected = set(raw_overlay) - {"model", "reasoning_effort", "approval_argv"}
        if unexpected:
            key = sorted(unexpected)[0]
            raise ValueError(
                f"agents.{agent_name}.roles.{role} only supports model, reasoning_effort, "
                "and approval_argv; "
                f"got {key}."
            )
        overlays.append(
            (
                role,
                RoleOverlay(
                    model=optional_str(raw_overlay.get("model")),
                    reasoning_effort=optional_str(raw_overlay.get("reasoning_effort")),
                    approval_argv=(
                        _string_tuple(raw_overlay["approval_argv"])
                        if "approval_argv" in raw_overlay
                        else None
                    ),
                ),
            )
        )
    return tuple(overlays)


def _model_prompts(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(model), str(prompt)) for model, prompt in value.items())


def _filter_disabled(
    items: tuple[tuple[str, _T], ...], disabled: tuple[str, ...]
) -> tuple[tuple[str, _T], ...]:
    if not disabled:
        return items
    disabled_names = set(disabled)
    return tuple((name, item) for name, item in items if name not in disabled_names)


def load_agent_settings(
    raw_config: dict[str, object],
) -> tuple[
    tuple[tuple[str, AgentRunConfig], ...],
    tuple[tuple[str, AgentPolicy], ...],
    tuple[tuple[str, tuple[tuple[str, RoleOverlay], ...]], ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    disabled_agents = _load_disabled_agents(
        raw_config.get("disabled_agents", IssuekitConfig.disabled_agents)
    )
    agents, policies, overlays = _load_agents(raw_config.get("agents", {}))
    agents = _filter_disabled(agents, disabled_agents)
    policies = _filter_disabled(policies, disabled_agents)
    overlays = _filter_disabled(overlays, disabled_agents)
    assignees = _load_assignees(raw_config, agents, disabled_agents)
    default_implementer = str(
        raw_config.get("default_implementer", IssuekitConfig.default_implementer)
    ).strip()
    _validate_not_disabled("default_implementer", default_implementer, disabled_agents)
    _validate_default_implementer(default_implementer, assignees)
    return agents, policies, overlays, disabled_agents, assignees
