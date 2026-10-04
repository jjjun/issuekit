"""Configuration source discovery, reading, and precedence."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from issuekit.config.local import LocalConfigError, load_toml, read_local_config
from issuekit.config.root import resolve_repository_root
from issuekit.gitutil import run_git

from .machine_filter import (
    _MACHINE_ONLY_AGENT_KEYS,
    _discard_unsupported_machine_config,
)
from .model import DEFAULT_PROFILE_FILE, IssuekitConfig, api_url_origin
from .values import _environment_value


def has_local_project_context(cwd: Path | str = ".") -> bool:
    """Return true when cwd looks like an issuekit project root."""

    config_cwd = resolve_repository_root(cwd)
    if (config_cwd / DEFAULT_PROFILE_FILE).is_file():
        return True

    pyproject_path = config_cwd / "pyproject.toml"
    if pyproject_path.exists():
        data = _load_config_toml(pyproject_path)
        pyproject_config = data.get("tool", {}).get("issuekit")
        if pyproject_config is not None:
            return True

    return (config_cwd / "issuekit.toml").is_file()


def resolve_machine_config_path() -> Path | None:
    configured = os.getenv("ISSUEKIT_CONFIG")
    if configured is not None:
        return Path(configured).expanduser() if configured else None
    xdg_config_home = _environment_value("XDG_CONFIG_HOME")
    config_home = (
        Path(xdg_config_home).expanduser()
        if xdg_config_home is not None
        else Path.home() / ".config"
    )
    return config_home / "issuekit" / "config.toml"


def _load_raw_config(
    cwd: Path, machine_path: Path | None
) -> tuple[dict[str, object], str, str, dict[str, object]]:
    machine_config = _load_machine_config(machine_path)
    raw_config = dict(machine_config)
    api_url_source = "machine_config" if "api_url" in raw_config else "none"
    repo_config_source = "none"
    pyproject_path = cwd / "pyproject.toml"
    if pyproject_path.exists():
        data = _load_config_toml(pyproject_path)
        pyproject_config = data.get("tool", {}).get("issuekit")
        if pyproject_config is not None:
            repo_config_source = "pyproject [tool.issuekit]"
            _reject_machine_only_repo_settings(pyproject_config, machine_path)
            _reject_repo_agent_launch_keys(
                pyproject_config,
                repo_config_source,
                machine_path,
                machine_config,
            )
            if "api_url" in pyproject_config:
                api_url_source = "repo_config"
            # pyproject's [tool.issuekit] wins when present so Python repos keep
            # their existing behavior even if a standalone config also exists.
            raw_config = _merge_config_layers(raw_config, dict(pyproject_config))

    issuekit_path = cwd / "issuekit.toml"
    if repo_config_source == "none" and issuekit_path.exists():
        repo_config_source = "issuekit.toml"
        issuekit_config = _load_config_toml(issuekit_path)
        _reject_machine_only_repo_settings(issuekit_config, machine_path)
        _reject_repo_agent_launch_keys(
            issuekit_config,
            repo_config_source,
            machine_path,
            machine_config,
        )
        if "api_url" in issuekit_config:
            api_url_source = "repo_config"
        raw_config = _merge_config_layers(raw_config, issuekit_config)

    return (
        _merge_local_config(cwd, raw_config),
        repo_config_source,
        api_url_source,
        machine_config,
    )


def _reject_tracked_dotenv(cwd: Path) -> None:
    if not (cwd / ".env").is_file():
        return
    result = run_git(["ls-files", "--error-unmatch", "--", ".env"], cwd)
    if result is not None and result.returncode == 0:
        raise ValueError(
            "Repo-local .env is tracked by git; issuekit does not load committed "
            "credentials or API settings. Untrack it (git rm --cached .env) and "
            "keep it local."
        )


def _reject_machine_only_repo_settings(
    config: dict[str, object], machine_path: Path | None
) -> None:
    path_label = str(machine_path) if machine_path is not None else "ISSUEKIT_CONFIG is empty"
    if "trusted_api_origins" in config:
        raise ValueError(f"trusted_api_origins can only be set in machine config ({path_label})")
    if "allow_insecure_api_url" in config:
        raise ValueError(
            "allow_insecure_api_url can only be set in machine config "
            f"({path_label}); set ISSUEKIT_ALLOW_INSECURE=1 in the process "
            "environment instead."
        )


def _reject_repo_agent_launch_keys(
    config: dict[str, object],
    source: str,
    machine_path: Path | None,
    machine_config: dict[str, object],
) -> None:
    path_label = str(machine_path) if machine_path is not None else "ISSUEKIT_CONFIG is empty"
    repo_agents = config.get("agents")
    if not isinstance(repo_agents, dict):
        return
    machine_agents = machine_config.get("agents")
    machine_agent_names = (
        {name for name, agent_config in machine_agents.items() if isinstance(agent_config, dict)}
        if isinstance(machine_agents, dict)
        else set()
    )
    built_in_agent_names = {name for name, _agent in IssuekitConfig.agents}
    for agent_name, agent_config in repo_agents.items():
        if not isinstance(agent_config, dict):
            continue
        if agent_name not in built_in_agent_names | machine_agent_names:
            raise ValueError(
                f"{source} defines agent '{agent_name}'; define new agents in "
                f"machine config ({path_label})."
            )
        for key in agent_config:
            if key in _MACHINE_ONLY_AGENT_KEYS:
                _reject_repo_agent_launch_key(source, f"agents.{agent_name}.{key}", path_label)
        roles = agent_config.get("roles")
        if not isinstance(roles, dict):
            continue
        for role, overlay in roles.items():
            if not isinstance(overlay, dict):
                continue
            for key in overlay:
                if key not in {"model", "reasoning_effort"}:
                    _reject_repo_agent_launch_key(
                        source,
                        f"agents.{agent_name}.roles.{role}.{key}",
                        path_label,
                    )


def _reject_repo_agent_launch_key(source: str, setting: str, path_label: str) -> None:
    raise ValueError(
        f"{source} sets {setting}; agent launch settings (binary, argv, approval "
        f"flags, runtime) can only be set in machine config ({path_label})."
    )


def _load_trusted_api_origins(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("trusted_api_origins must be a list of API URL origins.")
    origins: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("trusted_api_origins must contain only strings.")
        origin = api_url_origin(item.strip())
        if origin is None:
            raise ValueError(
                f"Invalid trusted_api_origins entry: {item}. Expected a URL with a scheme and host."
            )
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _trusted_api_origin_sources(
    process_api_url: str | None,
    machine_api_url: object,
    trusted_api_origins: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    if process_api_url is not None:
        origin = api_url_origin(process_api_url)
        if origin is not None:
            candidates.append((origin, "env"))
    if machine_api_url is not None:
        origin = api_url_origin(str(machine_api_url))
        if origin is not None:
            candidates.append((origin, "machine_config"))
    candidates.extend((origin, "trusted_api_origins") for origin in trusted_api_origins)
    return tuple(dict.fromkeys(candidates))


def _api_url_trusted_source(
    api_url_source: str,
    api_origin: str | None,
    trusted_origins: tuple[tuple[str, str], ...],
    repo_config_source: str,
    machine_path: Path | None,
) -> str:
    if api_url_source in {"env", "dotenv", "machine_config"}:
        return api_url_source
    if api_url_source != "repo_config":
        return "none"
    trusted_by = next(
        (source for origin, source in trusted_origins if origin == api_origin),
        None,
    )
    if trusted_by is not None:
        return trusted_by
    origin_display = api_origin or "(invalid origin)"
    trusted_display = (
        ", ".join(f"{origin} ({source})" for origin, source in trusted_origins) or "none"
    )
    path_label = str(machine_path) if machine_path is not None else "ISSUEKIT_CONFIG is empty"
    raise ValueError(
        f"api_url {origin_display} comes from {repo_config_source}, but it is not a "
        f"trusted API origin (trusted: {trusted_display}). Add it to "
        f"trusted_api_origins in {path_label}, or set api_url in machine config "
        "or ISSUEKIT_API_URL."
    )


def _load_machine_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        if not path.is_file():
            if os.getenv("ISSUEKIT_CONFIG"):
                print(
                    f"Warning: machine config file {path} was not found; "
                    "machine config is disabled.",
                    file=sys.stderr,
                )
            return {}
        config = _load_config_toml(path)
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise ValueError(
            f"Cannot read machine config {path}: {detail}. "
            "A sandboxed process may be denied access to the user profile config."
        ) from exc
    if "worker" in config:
        raise ValueError(f"Machine config {path} cannot define worker; use issuekit.local.toml")
    return _discard_unsupported_machine_config(config, path)


def _merge_config_layers(lower: dict[str, object], higher: dict[str, object]) -> dict[str, object]:
    merged = dict(lower)
    merged.update(higher)
    lower_agents = lower.get("agents")
    higher_agents = higher.get("agents")
    if isinstance(lower_agents, dict) and isinstance(higher_agents, dict):
        agents = dict(lower_agents)
        for name, value in higher_agents.items():
            previous = agents.get(name)
            if isinstance(previous, dict) and isinstance(value, dict):
                agents[name] = previous | value
            else:
                agents[name] = value
        merged["agents"] = agents
    return merged


def _merge_local_config(cwd: Path, raw_config: dict[str, object]) -> dict[str, object]:
    try:
        local_config = read_local_config(cwd)
    except LocalConfigError as exc:
        raise ValueError(str(exc)) from exc
    if local_config.worker is None and local_config.disabled_agents is None:
        return raw_config
    merged = dict(raw_config)
    if local_config.worker is not None:
        merged["worker"] = dict(local_config.worker)
    if local_config.disabled_agents is not None:
        merged["disabled_agents"] = list(local_config.disabled_agents)
    return merged


def _load_config_toml(path: Path) -> dict[str, object]:
    try:
        return load_toml(path)
    except LocalConfigError as exc:
        raise ValueError(str(exc)) from exc
