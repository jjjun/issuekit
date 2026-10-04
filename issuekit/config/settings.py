"""Top-level configuration loading for issuekit."""

from __future__ import annotations

from pathlib import Path

from issuekit.config.dotenv import is_loaded_from_dotenv, load_dotenv
from issuekit.config.root import resolve_repository_root

from .agents import (
    _load_agent_roles,
    _validate_not_disabled,
    load_agent_settings,
)
from .model import IssuekitConfig, api_url_origin
from .sections import (
    _load_router_policy,
    _load_triage_policy,
    load_identity_metadata,
)
from .sources import (
    _api_url_trusted_source,
    _load_raw_config,
    _load_trusted_api_origins,
    _reject_tracked_dotenv,
    _trusted_api_origin_sources,
    resolve_machine_config_path,
)
from .values import (
    _bool_value,
    _config_value,
    _environment_value,
    _string_tuple,
    _validate_claim_sync_interval,
    _validate_project,
    _validate_work_branch,
    _validate_worker_heartbeat_interval,
)

_SENTINEL = object()


def load_config(cwd: Path | str = ".") -> IssuekitConfig:
    config_cwd = resolve_repository_root(cwd)
    _reject_tracked_dotenv(config_cwd)
    load_dotenv(config_cwd)
    machine_path = resolve_machine_config_path()
    raw_config, repo_config_source, config_api_url_source, machine_config = _load_raw_config(
        config_cwd, machine_path
    )
    api_settings = _resolve_api_url(
        raw_config, config_api_url_source, machine_config, repo_config_source, machine_path
    )
    return _build_config(raw_config, api_settings, machine_path, repo_config_source)


def _build_config(
    raw_config: dict[str, object],
    api_settings: dict[str, object],
    machine_path: Path | None,
    repo_config_source: str,
) -> IssuekitConfig:
    identity_settings = load_identity_metadata(raw_config)
    worker = identity_settings["worker"]
    project = _resolve_project(raw_config, worker)
    agents, policies, overlays, disabled, assignees = load_agent_settings(raw_config)
    _interval_settings = _load_intervals(raw_config)
    triage = _load_triage_policy(raw_config.get("triage", {}))
    router = _load_router_policy(raw_config.get("router", {}))
    agent_roles = _load_agent_roles(raw_config.get("agent_roles"))
    _validate_not_disabled("router.agent", router.agent, disabled)
    _validate_not_disabled("triage.author_agent", triage.author_agent, disabled)
    return IssuekitConfig(
        **api_settings,
        project=project,
        api_timeout=_config_value(
            "api_timeout",
            _environment_value("ISSUEKIT_API_TIMEOUT")
            or raw_config.get("api_timeout", IssuekitConfig.api_timeout),
            float,
        ),
        assignees=assignees,
        stages=_string_tuple(raw_config.get("stages", IssuekitConfig.stages)),
        default_implementer=str(
            raw_config.get("default_implementer", IssuekitConfig.default_implementer)
        ).strip(),
        work_branch=_validated_work_branch(raw_config),
        gate_halfwidth_kana=_bool_value(
            raw_config.get("gate_halfwidth_kana", IssuekitConfig.gate_halfwidth_kana)
        ),
        check_encoding_exclude=_string_tuple(
            raw_config.get("check_encoding_exclude", IssuekitConfig.check_encoding_exclude)
        ),
        claim_sync=_bool_value(raw_config.get("claim_sync", IssuekitConfig.claim_sync)),
        **_interval_settings,
        **identity_settings,
        triage=triage,
        router=router,
        agent_roles=agent_roles,
        disabled_agents=disabled,
        agent_role_overlays=overlays,
        machine_config_path=(
            machine_path if machine_path is not None and machine_path.is_file() else None
        ),
        repo_config_source=repo_config_source,
        agents=agents,
        agent_policies=policies,
    )


def _resolve_api_url(
    raw_config: dict[str, object],
    config_api_url_source: str,
    machine_config: dict[str, object],
    repo_config_source: str,
    machine_path: Path | None,
) -> dict[str, object]:
    trusted_api_origins = _load_trusted_api_origins(machine_config.get("trusted_api_origins", ()))
    api_url_env = _environment_value("ISSUEKIT_API_URL")
    process_api_url_env = (
        api_url_env
        if api_url_env is not None and not is_loaded_from_dotenv("ISSUEKIT_API_URL")
        else None
    )
    api_url_source = (
        ("dotenv" if is_loaded_from_dotenv("ISSUEKIT_API_URL") else "env")
        if api_url_env is not None
        else config_api_url_source
    )
    api_url = str(
        api_url_env
        if api_url_env is not None
        else raw_config.get("api_url", IssuekitConfig.api_url)
    ).strip()
    trusted_origins = _trusted_api_origin_sources(
        process_api_url_env, machine_config.get("api_url"), trusted_api_origins
    )
    api_url_trusted_by = _api_url_trusted_source(
        api_url_source,
        api_url_origin(api_url),
        trusted_origins,
        repo_config_source,
        machine_path,
    )
    return {
        "api_url": api_url,
        "trusted_api_origins": trusted_api_origins,
        "allow_insecure_api_url": _bool_value(machine_config.get("allow_insecure_api_url", False)),
        "api_url_source": api_url_source,
        "api_url_trusted_by": api_url_trusted_by,
    }


def _resolve_project(raw_config: dict[str, object], worker: object) -> str:
    configured_project = raw_config.get("project", _SENTINEL)
    project_env = _environment_value("ISSUEKIT_PROJECT")
    default_project = (
        configured_project
        if configured_project is not _SENTINEL
        else worker.repo_id
        if worker is not None
        else IssuekitConfig.project
    )
    project = str(project_env if project_env is not None else default_project).strip()
    _validate_project(project)
    return project


def _load_intervals(raw_config: dict[str, object]) -> dict[str, float]:
    claim_sync_interval_sec = _config_value(
        "claim_sync_interval_sec",
        raw_config.get("claim_sync_interval_sec", IssuekitConfig.claim_sync_interval_sec),
        float,
    )
    _validate_claim_sync_interval(claim_sync_interval_sec)
    worker_heartbeat_interval_sec = _config_value(
        "worker_heartbeat_interval_sec",
        raw_config.get(
            "worker_heartbeat_interval_sec",
            IssuekitConfig.worker_heartbeat_interval_sec,
        ),
        float,
    )
    _validate_worker_heartbeat_interval(worker_heartbeat_interval_sec)
    return {
        "claim_sync_interval_sec": claim_sync_interval_sec,
        "worker_heartbeat_interval_sec": worker_heartbeat_interval_sec,
    }


def _validated_work_branch(raw_config: dict[str, object]) -> str:
    work_branch = str(raw_config.get("work_branch", IssuekitConfig.work_branch)).strip()
    _validate_work_branch(work_branch)
    return work_branch
