"""Configuration loading for issuekit."""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass, field, fields, replace
from pathlib import Path, PureWindowsPath
from string import Formatter
from urllib.parse import urlparse

from issuekit.agentrun.config import AgentRunConfig
from issuekit.config.root import resolve_repository_root
from issuekit.core import (
    VALID_ISSUE_PRIORITIES,
    WORKFLOW_TOKEN_MAX_LEN,
    is_valid_workflow_token,
    optional_int,
    optional_str,
    qualified_worker_key,
    worker_key,
)
from issuekit.encoding import has_non_ascii
from issuekit.gitutil import run_git
from issuekit.worker_constants import WORKER_HEARTBEAT_INTERVAL_SEC

from .dotenv import is_loaded_from_dotenv, load_dotenv
from .local import LocalConfigError, load_toml, read_local_config

_SENTINEL = object()
_IMPLEMENTER_PROMPT_SUFFIX = (
    "Make minimal, additive diffs. Do not reformat, re-quote, "
    "re-order imports, or rewrite/translate comments on lines "
    "unrelated to your change.\n"
    "Never alter existing non-ASCII (e.g. Japanese) text that the "
    "task does not ask you to change. When the task asks you to "
    "correct such text, rewrite it in place in the same language "
    "unless the task asks for a translation, keep the file encoding "
    "(UTF-8, no BOM, LF), and do not keep the replaced text in a "
    "comment. Preserve existing comments byte-for-byte unless the "
    "task is specifically to change them. After editing, verify "
    "you introduced no mojibake.\n"
    "When a task says 'add X alongside Y, do not change Y,' the diff "
    "must touch only the added region; if you cannot, stop and report "
    "instead of reformatting."
)

# Repo-level worker metadata length limits agreed with the mine-py backend
# (negotiation thread 18): role stays short, description allows a sentence or two.
WORKER_ROLE_MAX_LEN = 80
WORKER_DESCRIPTION_MAX_LEN = 500
REPO_DESCRIPTION_MAX_LEN = 500

# Project-level capability profile limits (mine-py#172). The long-form profile
# lives in a committed markdown file; summary/tags are short optional metadata.
DEFAULT_PROFILE_FILE = "ISSUEKIT.md"
PROFILE_SUMMARY_MAX_LEN = 500
PROFILE_TAG_MAX_LEN = WORKFLOW_TOKEN_MAX_LEN
PROFILE_TAGS_MAX = 20
AGENT_ROLES = frozenset({"author", "implementer", "pm", "reviewer", "triage"})
ROLE_OVERLAY_ROLES = frozenset(
    {"implementer", "reviewer", "router", "triage", "negotiation"}
)
READ_ONLY_ROLES = frozenset({"reviewer", "router", "triage", "negotiation"})
_CLAUDE_READ_ONLY_APPROVAL_ARGV = (
    "--permission-mode",
    "dontAsk",
    "--allowedTools",
    "Read,Grep,Glob,Bash(git status:*),Bash(git diff:*),Bash(git log:*),"
    "Bash(git show:*),Bash(git ls-files:*)",
    "--strict-mcp-config",
)
BUILTIN_ROLE_LAUNCH_POLICIES = {
    "codex": {
        "triage": ("--sandbox", "read-only", "-c", "mcp_servers={}"),
        "router": ("--sandbox", "read-only", "-c", "mcp_servers={}"),
        "negotiation": ("--sandbox", "read-only", "-c", "mcp_servers={}"),
        "reviewer": ("--sandbox", "workspace-write", "-c", "mcp_servers={}"),
    },
    "claude": dict.fromkeys(READ_ONLY_ROLES, _CLAUDE_READ_ONLY_APPROVAL_ARGV),
}


@dataclass(frozen=True)
class AgentPolicy:
    """Issuekit submission policy applied to an agent's implementation."""

    mojibake_gate: bool = False
    diff_shape_warn_deletions: int | None = None


@dataclass(frozen=True)
class RoleOverlay:
    """Model and launch settings that apply when an agent runs in one role."""

    model: str | None = None
    reasoning_effort: str | None = None
    approval_argv: tuple[str, ...] | None = None


@dataclass(frozen=True)
class WorkerIdentity:
    machine_id: str
    repo_id: str
    worker_id: str

    @property
    def worker_name(self) -> str:
        return self.worker_id


@dataclass(frozen=True)
class TriagePolicy:
    """Target-owned policy for automatic inbox proposal adoption."""

    auto_adopt: bool = False
    hold_auto_adopted: bool = True
    trusted_origins: tuple[str, ...] = ()
    default_priority: str = "medium"
    require_blocking: bool = False
    max_adoptions_per_cycle: int = 5
    author_agent: str = ""


@dataclass(frozen=True)
class RouterPolicy:
    """PM router policy for request-to-proposal routing."""

    agent: str = ""
    max_targets: int = 3
    max_clarify_rounds: int = 2


@dataclass(frozen=True)
class IssuekitConfig:
    api_url: str = ""
    trusted_api_origins: tuple[str, ...] = ()
    allow_insecure_api_url: bool = False
    project: str = "issuekit"
    api_timeout: float = 30.0
    issues_dir: str = "docs/issues"
    assignees: tuple[str, ...] = ("codex", "claude", "kimi")
    stages: tuple[str, ...] = (
        "planned",
        "todo",
        "implementing",
        "review",
        "changes_requested",
        "done",
    )
    default_reviewer: str = "claude"
    default_implementer: str = ""
    require_distinct_reviewer: bool = False
    work_branch: str = ""
    gate_halfwidth_kana: bool = True
    check_encoding_exclude: tuple[str, ...] = ()
    claim_sync: bool = True
    claim_sync_interval_sec: float = 60.0
    worker_heartbeat_interval_sec: float = WORKER_HEARTBEAT_INTERVAL_SEC
    send_agent_runtime: bool = True
    worker: WorkerIdentity | None = None
    worker_role: str = ""
    worker_description: str = ""
    worker_accept_directed: bool = False
    repo_description: str = ""
    repo_metadata: dict[str, str] = field(default_factory=dict)
    worker_metadata: dict[str, str] = field(default_factory=dict)
    profile_file: str = DEFAULT_PROFILE_FILE
    profile_summary: str = ""
    profile_tags: tuple[str, ...] = ()
    triage: TriagePolicy = field(default_factory=TriagePolicy)
    router: RouterPolicy = field(default_factory=RouterPolicy)
    agent_roles: dict[str, str] = field(default_factory=dict)
    disabled_agents: tuple[str, ...] = ()
    agent_role_overlays: tuple[tuple[str, tuple[tuple[str, RoleOverlay], ...]], ...] = ()
    machine_config_path: Path | None = None
    repo_config_source: str = field(default="none", compare=False)
    api_url_source: str = field(default="none", compare=False)
    api_url_trusted_by: str = field(default="none", compare=False)
    agents: tuple[tuple[str, AgentRunConfig], ...] = (
        (
            "kimi",
            AgentRunConfig(
                binary="kimi",
                adapter="kimi",
                known_paths=("~/.kimi-code/bin/kimi", "~/.kimi-code/bin/kimi.exe"),
                headless_argv=("-p",),
                output_format_flag="--output-format",
                output_format="text",
                model_flag="-m",
            ),
        ),
        (
            "codex",
            AgentRunConfig(
                binary="codex",
                adapter="codex",
                known_paths=(
                    "~/.codex/.sandbox-bin/codex",
                    "~/.codex/.sandbox-bin/codex.exe",
                ),
                headless_argv=("exec",),
                approval_flag="--dangerously-bypass-approvals-and-sandbox",
                model_flag="--model",
                effort_argv=("-c", "model_reasoning_effort={value}"),
                speed_argv=("-c", "service_tier=fast"),
                prompt_suffix=_IMPLEMENTER_PROMPT_SUFFIX,
            ),
        ),
        (
            "claude",
            AgentRunConfig(
                binary="claude",
                known_paths=(
                    "~/.claude/local/claude",
                    "~/.claude/local/claude.exe",
                    "~/.local/bin/claude",
                    "~/.local/bin/claude.exe",
                ),
                headless_argv=("-p",),
                resumable=True,
                session_flag="--session-id",
                resume_flag="--resume",
                approval_flag="--permission-mode",
                approval_value="bypassPermissions",
                output_format_flag="--output-format",
                output_format="json",
                model_flag="--model",
                effort_argv=("--effort", "{value}"),
                speed_argv=("--settings", '{"fastMode": true}'),
                prompt_suffix=_IMPLEMENTER_PROMPT_SUFFIX,
            ),
        ),
    )
    agent_policies: tuple[tuple[str, AgentPolicy], ...] = (
        ("codex", AgentPolicy(mojibake_gate=True, diff_shape_warn_deletions=40)),
        ("claude", AgentPolicy(mojibake_gate=True, diff_shape_warn_deletions=40)),
    )

    def issues_path(self, cwd: Path | str = ".") -> Path:
        path = Path(self.issues_dir)
        if path.is_absolute():
            return path
        return Path(cwd) / path

    def worker_key(self) -> str | None:
        if self.worker is None:
            return None
        return worker_key(self.worker.repo_id, self.worker.worker_name)

    def qualified_worker_key(self) -> str | None:
        if self.worker is None:
            return None
        return qualified_worker_key(
            self.worker.machine_id,
            self.worker.repo_id,
            self.worker.worker_name,
        )

    def worker_lookup_keys(self) -> tuple[str, ...]:
        qualified = self.qualified_worker_key()
        current = self.worker_key()
        return tuple(key for key in (qualified, current) if key)


def load_config(cwd: Path | str = ".") -> IssuekitConfig:
    config_cwd = resolve_repository_root(cwd)
    _reject_tracked_dotenv(config_cwd)
    load_dotenv(config_cwd)
    machine_path = resolve_machine_config_path()
    (
        raw_config,
        repo_config_source,
        config_api_url_source,
        machine_config,
    ) = _load_raw_config(config_cwd, machine_path)
    trusted_api_origins = _load_trusted_api_origins(
        machine_config.get("trusted_api_origins", ())
    )
    api_url_env = _environment_value("ISSUEKIT_API_URL")
    process_api_url_env = (
        api_url_env
        if api_url_env is not None and not is_loaded_from_dotenv("ISSUEKIT_API_URL")
        else None
    )
    if api_url_env is not None:
        api_url_source = (
            "dotenv" if is_loaded_from_dotenv("ISSUEKIT_API_URL") else "env"
        )
    else:
        api_url_source = config_api_url_source
    api_url = str(
        api_url_env
        if api_url_env is not None
        else raw_config.get("api_url", IssuekitConfig.api_url)
    ).strip()
    api_url_origin_value = api_url_origin(api_url)
    trusted_origins = _trusted_api_origin_sources(
        process_api_url_env,
        machine_config.get("api_url"),
        trusted_api_origins,
    )
    api_url_trusted_by = _api_url_trusted_source(
        api_url_source,
        api_url_origin_value,
        trusted_origins,
        repo_config_source,
        machine_path,
    )
    allow_insecure_api_url = _bool_value(
        machine_config.get("allow_insecure_api_url", False)
    )
    worker = _load_worker(raw_config.get("worker"))
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
    disabled_agents = _load_disabled_agents(
        raw_config.get("disabled_agents", IssuekitConfig.disabled_agents)
    )
    agents, agent_policies, agent_role_overlays = _load_agents(raw_config.get("agents", {}))
    agents = _filter_disabled_agents(agents, disabled_agents)
    agent_policies = _filter_disabled_agent_policies(agent_policies, disabled_agents)
    agent_role_overlays = _filter_disabled_agent_role_overlays(
        agent_role_overlays, disabled_agents
    )
    assignees = _load_assignees(raw_config, agents, disabled_agents)
    default_reviewer = (
        "auto"
        if api_url
        else str(raw_config.get("default_reviewer", IssuekitConfig.default_reviewer)).strip()
    )
    _validate_not_disabled("default_reviewer", default_reviewer, disabled_agents)
    _validate_default_reviewer(default_reviewer, assignees)
    default_implementer = str(
        raw_config.get("default_implementer", IssuekitConfig.default_implementer)
    ).strip()
    _validate_not_disabled("default_implementer", default_implementer, disabled_agents)
    _validate_default_implementer(default_implementer, assignees)
    work_branch = str(raw_config.get("work_branch", IssuekitConfig.work_branch)).strip()
    _validate_work_branch(work_branch)
    claim_sync_interval_sec = _float_config_value(
        "claim_sync_interval_sec",
        raw_config.get(
            "claim_sync_interval_sec", IssuekitConfig.claim_sync_interval_sec
        ),
    )
    _validate_claim_sync_interval(claim_sync_interval_sec)
    worker_heartbeat_interval_sec = _float_config_value(
        "worker_heartbeat_interval_sec",
        raw_config.get(
            "worker_heartbeat_interval_sec",
            IssuekitConfig.worker_heartbeat_interval_sec,
        ),
    )
    _validate_worker_heartbeat_interval(worker_heartbeat_interval_sec)
    triage = _load_triage_policy(raw_config.get("triage", {}))
    router = _load_router_policy(raw_config.get("router", {}))
    agent_roles = _load_agent_roles(raw_config.get("agent_roles"))
    _validate_not_disabled("router.agent", router.agent, disabled_agents)
    _validate_not_disabled("triage.author_agent", triage.author_agent, disabled_agents)
    worker_role = _worker_metadata(
        raw_config.get("worker_role"), field="worker_role", max_len=WORKER_ROLE_MAX_LEN
    )
    worker_description = _worker_metadata(
        raw_config.get("worker_description"),
        field="worker_description",
        max_len=WORKER_DESCRIPTION_MAX_LEN,
    )
    repo_description = _worker_metadata(
        raw_config.get("repo_description"),
        field="repo_description",
        max_len=REPO_DESCRIPTION_MAX_LEN,
    )
    repo_metadata = _metadata_table(raw_config.get("repo_metadata"), field="repo_metadata")
    worker_metadata = _metadata_table(
        raw_config.get("worker_metadata"), field="worker_metadata"
    )
    profile_file = str(
        raw_config.get("profile_file", IssuekitConfig.profile_file)
    ).strip() or IssuekitConfig.profile_file
    profile_summary = _worker_metadata(
        raw_config.get("profile_summary"),
        field="profile_summary",
        max_len=PROFILE_SUMMARY_MAX_LEN,
    )
    profile_tags = _load_profile_tags(raw_config.get("profile_tags"))
    return IssuekitConfig(
        api_url=api_url,
        trusted_api_origins=trusted_api_origins,
        allow_insecure_api_url=allow_insecure_api_url,
        project=project,
        api_timeout=_float_config_value(
            "api_timeout",
            _environment_value("ISSUEKIT_API_TIMEOUT")
            or raw_config.get("api_timeout", IssuekitConfig.api_timeout),
        ),
        issues_dir=str(raw_config.get("issues_dir", IssuekitConfig.issues_dir)),
        assignees=assignees,
        stages=_string_tuple(raw_config.get("stages", IssuekitConfig.stages)),
        default_reviewer=default_reviewer,
        default_implementer=default_implementer,
        require_distinct_reviewer=_bool_value(
            True
            if api_url
            else raw_config.get(
                "require_distinct_reviewer",
                IssuekitConfig.require_distinct_reviewer,
            )
        ),
        work_branch=work_branch,
        gate_halfwidth_kana=_bool_value(
            raw_config.get("gate_halfwidth_kana", IssuekitConfig.gate_halfwidth_kana)
        ),
        check_encoding_exclude=_string_tuple(
            raw_config.get(
                "check_encoding_exclude", IssuekitConfig.check_encoding_exclude
            )
        ),
        claim_sync=_bool_value(raw_config.get("claim_sync", IssuekitConfig.claim_sync)),
        claim_sync_interval_sec=claim_sync_interval_sec,
        worker_heartbeat_interval_sec=worker_heartbeat_interval_sec,
        send_agent_runtime=_bool_value(
            raw_config.get("send_agent_runtime", IssuekitConfig.send_agent_runtime)
        ),
        worker=worker,
        worker_role=worker_role,
        worker_description=worker_description,
        worker_accept_directed=_bool_value(
            raw_config.get(
                "worker_accept_directed",
                IssuekitConfig.worker_accept_directed,
            )
        ),
        repo_description=repo_description,
        repo_metadata=repo_metadata,
        worker_metadata=worker_metadata,
        profile_file=profile_file,
        profile_summary=profile_summary,
        profile_tags=profile_tags,
        triage=triage,
        router=router,
        agent_roles=agent_roles,
        disabled_agents=disabled_agents,
        agent_role_overlays=agent_role_overlays,
        machine_config_path=machine_path if machine_path is not None and machine_path.is_file() else None,
        repo_config_source=repo_config_source,
        api_url_source=api_url_source,
        api_url_trusted_by=api_url_trusted_by,
        agents=agents,
        agent_policies=agent_policies,
    )


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
            _reject_machine_only_repo_settings(
                pyproject_config, machine_path
            )
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
        raise ValueError(
            f"trusted_api_origins can only be set in machine config ({path_label})"
        )
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
    path_label = (
        str(machine_path) if machine_path is not None else "ISSUEKIT_CONFIG is empty"
    )
    repo_agents = config.get("agents")
    if not isinstance(repo_agents, dict):
        return
    machine_agents = machine_config.get("agents")
    machine_agent_names = (
        {
            name
            for name, agent_config in machine_agents.items()
            if isinstance(agent_config, dict)
        }
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
                _reject_repo_agent_launch_key(
                    source, f"agents.{agent_name}.{key}", path_label
                )
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
    trusted_display = ", ".join(
        f"{origin} ({source})" for origin, source in trusted_origins
    ) or "none"
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
        raise ValueError(
            f"Machine config {path} cannot define worker; use issuekit.local.toml"
        )
    return _discard_unsupported_machine_config(config, path)


def _environment_value(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _float_config_value(name: str, value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: {exc}") from exc


def _int_config_value(name: str, value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: {exc}") from exc


def api_url_origin(api_url: str) -> str | None:
    """Return the API URL origin without userinfo, path, query, or fragment."""
    try:
        parsed = urlparse(api_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not parsed.scheme or not hostname:
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None else f"{host}:{port}"
    return f"{parsed.scheme}://{netloc}"


def _discard_unsupported_machine_config(
    config: dict[str, object], path: Path
) -> dict[str, object]:
    sanitized = {
        key: value
        for key, value in config.items()
        if _keep_machine_setting(path, str(key), str(key), _MACHINE_CONFIG_KEYS)
    }
    _discard_unsupported_table_keys(
        sanitized, "triage", _TRIAGE_CONFIG_KEYS, path
    )
    _discard_unsupported_table_keys(
        sanitized, "router", _ROUTER_CONFIG_KEYS, path
    )
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
        if str(role) not in ROLE_OVERLAY_ROLES:
            _keep_machine_setting(
                path,
                f"agents.{agent_name}.roles.{role}",
                str(role),
                ROLE_OVERLAY_ROLES,
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
            path, f"agent_roles.{agent} = {role}", str(role), AGENT_ROLES
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


def _merge_config_layers(
    lower: dict[str, object], higher: dict[str, object]
) -> dict[str, object]:
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


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return tuple(str(value).split()) if isinstance(value, str) else ()


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


def _filter_disabled_agents(
    agents: tuple[tuple[str, AgentRunConfig], ...],
    disabled_agents: tuple[str, ...],
) -> tuple[tuple[str, AgentRunConfig], ...]:
    if not disabled_agents:
        return agents
    disabled = set(disabled_agents)
    return tuple((name, cfg) for name, cfg in agents if name not in disabled)


def _filter_disabled_agent_policies(
    policies: tuple[tuple[str, AgentPolicy], ...],
    disabled_agents: tuple[str, ...],
) -> tuple[tuple[str, AgentPolicy], ...]:
    if not disabled_agents:
        return policies
    disabled = set(disabled_agents)
    return tuple((name, policy) for name, policy in policies if name not in disabled)


def _filter_disabled_agent_role_overlays(
    overlays: tuple[tuple[str, tuple[tuple[str, RoleOverlay], ...]], ...],
    disabled_agents: tuple[str, ...],
) -> tuple[tuple[str, tuple[tuple[str, RoleOverlay], ...]], ...]:
    if not disabled_agents:
        return overlays
    disabled = set(disabled_agents)
    return tuple((name, roles) for name, roles in overlays if name not in disabled)


def _dedupe_tokens(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _validate_not_disabled(
    field: str,
    value: str,
    disabled_agents: tuple[str, ...],
) -> None:
    if value and value in set(disabled_agents):
        raise ValueError(f"{field} references disabled agent: {value}")


def _validate_default_reviewer(default_reviewer: str, assignees: tuple[str, ...]) -> None:
    if not is_valid_workflow_token(default_reviewer):
        raise ValueError(f"Invalid default_reviewer token: {default_reviewer}")
    if default_reviewer == "auto":
        return
    if default_reviewer not in assignees:
        raise ValueError(f"Unknown default_reviewer: {default_reviewer}")


def _validate_default_implementer(
    default_implementer: str, assignees: tuple[str, ...]
) -> None:
    if not default_implementer:
        return
    if not is_valid_workflow_token(default_implementer):
        raise ValueError(f"Invalid default_implementer token: {default_implementer}")
    if default_implementer not in assignees:
        raise ValueError(f"Unknown default_implementer: {default_implementer}")


def _validate_project(project: str) -> None:
    if not project or not is_valid_workflow_token(project):
        raise ValueError(f"Invalid project token: {project}")


def _validate_work_branch(work_branch: str) -> None:
    if not work_branch:
        return
    components = work_branch.split("/")
    invalid = (
        has_non_ascii(work_branch)
        or work_branch.startswith("-")
        or work_branch == "@"
        or ".." in work_branch
        or "@{" in work_branch
        or "//" in work_branch
        or work_branch.startswith("/")
        or work_branch.endswith("/")
        or any(
            char.isspace()
            or ord(char) < 32
            or ord(char) == 127
            or char in "~^:?*[\\"
            for char in work_branch
        )
        or any(
            component.startswith(".")
            or component.endswith(".")
            or component.endswith(".lock")
            for component in components
        )
    )
    if invalid:
        raise ValueError(
            f"Invalid work_branch: {work_branch} is not a valid git branch name."
        )


def _validate_claim_sync_interval(value: float) -> None:
    if value < 0:
        raise ValueError("claim_sync_interval_sec must be zero or greater.")


def _validate_worker_heartbeat_interval(value: float) -> None:
    if value <= 0:
        raise ValueError("worker_heartbeat_interval_sec must be greater than zero.")


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


def _load_worker(raw: object) -> WorkerIdentity | None:
    if not isinstance(raw, dict):
        return None
    machine_id = _required_worker_value(raw, "machine_id")
    repo_id = _required_worker_value(raw, "repo_id")
    worker_id = _required_worker_value(raw, "worker_name") or _required_worker_value(
        raw, "worker_id"
    )
    if not (machine_id and repo_id and worker_id):
        return None
    return WorkerIdentity(machine_id=machine_id, repo_id=repo_id, worker_id=worker_id)


def _worker_metadata(value: object, *, field: str, max_len: int) -> str:
    text = optional_str(value)
    if text is None:
        return ""
    if len(text) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters.")
    return text


def _metadata_table(value: object, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a table.")
    metadata: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or not is_valid_workflow_token(key):
            raise ValueError(f"Invalid {field} key: {raw_key}")
        text = optional_str(raw_value)
        if text is None:
            continue
        metadata[key] = text
    return metadata


def _load_profile_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    tags = _string_tuple(value)
    if len(tags) > PROFILE_TAGS_MAX:
        raise ValueError(f"profile_tags must have at most {PROFILE_TAGS_MAX} tags.")
    for tag in tags:
        if not tag or len(tag) > PROFILE_TAG_MAX_LEN or not is_valid_workflow_token(tag):
            raise ValueError(f"Invalid profile_tags token: {tag}")
    return tags


def _load_triage_policy(raw: object) -> TriagePolicy:
    if not raw:
        return TriagePolicy()
    if not isinstance(raw, dict):
        raise ValueError("triage config must be a table.")
    hold_auto_adopted = raw.get(
        "hold_auto_adopted",
        TriagePolicy.hold_auto_adopted,
    )
    if not isinstance(hold_auto_adopted, bool):
        raise ValueError("Invalid triage.hold_auto_adopted: must be a boolean.")
    default_priority = str(raw.get("default_priority", TriagePolicy.default_priority)).strip()
    if default_priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid triage.default_priority: {default_priority}")
    trusted_origins = _string_tuple(
        raw.get("trusted_origins", TriagePolicy.trusted_origins)
    )
    invalid_origins = [
        origin
        for origin in trusted_origins
        if not origin or not is_valid_workflow_token(origin)
    ]
    if invalid_origins:
        raise ValueError(f"Invalid triage.trusted_origins token: {invalid_origins[0]}")
    max_adoptions = _int_config_value(
        "triage.max_adoptions_per_cycle",
        raw.get(
            "max_adoptions_per_cycle",
            TriagePolicy.max_adoptions_per_cycle,
        )
    )
    if max_adoptions < 1:
        raise ValueError("triage.max_adoptions_per_cycle must be greater than zero.")
    author_agent = str(raw.get("author_agent", TriagePolicy.author_agent)).strip()
    if author_agent and not is_valid_workflow_token(author_agent):
        raise ValueError(f"Invalid triage.author_agent token: {author_agent}")
    return TriagePolicy(
        auto_adopt=_bool_value(raw.get("auto_adopt", TriagePolicy.auto_adopt)),
        hold_auto_adopted=hold_auto_adopted,
        trusted_origins=trusted_origins,
        default_priority=default_priority,
        require_blocking=_bool_value(
            raw.get("require_blocking", TriagePolicy.require_blocking)
        ),
        max_adoptions_per_cycle=max_adoptions,
        author_agent=author_agent,
    )


def _load_router_policy(raw: object) -> RouterPolicy:
    if not raw:
        return RouterPolicy()
    if not isinstance(raw, dict):
        raise ValueError("router config must be a table.")
    agent = str(raw.get("agent", RouterPolicy.agent)).strip()
    if agent and not is_valid_workflow_token(agent):
        raise ValueError(f"Invalid router.agent token: {agent}")
    max_targets = _int_config_value(
        "router.max_targets", raw.get("max_targets", RouterPolicy.max_targets)
    )
    if max_targets < 1:
        raise ValueError("router.max_targets must be greater than zero.")
    max_clarify_rounds = _int_config_value(
        "router.max_clarify_rounds",
        raw.get("max_clarify_rounds", RouterPolicy.max_clarify_rounds),
    )
    if max_clarify_rounds < 0:
        raise ValueError("router.max_clarify_rounds must be zero or greater.")
    return RouterPolicy(
        agent=agent,
        max_targets=max_targets,
        max_clarify_rounds=max_clarify_rounds,
    )


def _required_worker_value(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    return "" if value is None else str(value).strip()


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
            _validate_agent_command_or_path(path, "known_paths")
            for path in known_paths
        )
    effort_argv = overrides.get("effort_argv")
    if effort_argv is not None:
        _validate_effort_argv(effort_argv)
    runtime = overrides.get("runtime")
    if runtime is not None and runtime not in {"exec", "codex_app_server"}:
        raise ValueError(
            "Agent runtime must be 'exec' or 'codex_app_server'."
        )
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
        raise ValueError(
            f"{setting} must be a bare command name or an absolute path."
        )
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
            if field_name is not None and (
                field_name != "value" or format_spec or conversion
            ):
                raise ValueError(
                    "Invalid effort_argv template: only the {value} placeholder "
                    "is allowed."
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
        if role not in ROLE_OVERLAY_ROLES:
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


def parse_bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean config value: {value}")


def _bool_value(value: object) -> bool:
    return parse_bool_value(value)
