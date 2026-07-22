"""Configuration loading for issuekit."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from pathlib import Path

from issuekit.core import (
    VALID_ISSUE_PRIORITIES,
    is_valid_workflow_token,
    optional_int,
    optional_str,
    qualified_worker_key,
    worker_key,
)
from issuekit.agentrun.config import AgentRunConfig
from issuekit.encoding import has_non_ascii
from .dotenv import load_dotenv
from .local import LocalConfigError, load_toml, read_local_config


_SENTINEL = object()

# Repo-level worker metadata length limits agreed with the mine-py backend
# (negotiation thread 18): role stays short, description allows a sentence or two.
WORKER_ROLE_MAX_LEN = 80
WORKER_DESCRIPTION_MAX_LEN = 500
REPO_DESCRIPTION_MAX_LEN = 500

# Project-level capability profile limits (mine-py#172). The long-form profile
# lives in a committed markdown file; summary/tags are short optional metadata.
DEFAULT_PROFILE_FILE = "ISSUEKIT.md"
PROFILE_SUMMARY_MAX_LEN = 500
PROFILE_TAG_MAX_LEN = 40
PROFILE_TAGS_MAX = 20


@dataclass(frozen=True)
class AgentPolicy:
    """Issuekit submission policy applied to an agent's implementation."""

    mojibake_gate: bool = False
    diff_shape_warn_deletions: int | None = None


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
    project: str = "issuekit"
    api_timeout: float = 30.0
    ascii_id_threshold: int = 0
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
    require_distinct_reviewer: bool = False
    work_branch: str = ""
    gate_halfwidth_kana: bool = True
    check_encoding_exclude: tuple[str, ...] = ()
    claim_sync: bool = True
    claim_sync_interval_sec: float = 60.0
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
    disabled_agents: tuple[str, ...] = ()
    machine_config_path: Path | None = None
    repo_config_source: str = field(default="none", compare=False)
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
                known_paths=(
                    "~/.codex/.sandbox-bin/codex",
                    "~/.codex/.sandbox-bin/codex.exe",
                ),
                headless_argv=("exec",),
                approval_flag="--dangerously-bypass-approvals-and-sandbox",
                model_flag="--model",
                effort_argv=("-c", "model_reasoning_effort={value}"),
                prompt_suffix=(
                    "Make minimal, additive diffs. Do not reformat, re-quote, "
                    "re-order imports, or rewrite/translate comments on lines "
                    "unrelated to your change.\n"
                    "Never alter existing non-ASCII (e.g. Japanese) text. Preserve "
                    "existing comments byte-for-byte unless the task is specifically "
                    "to change them. After editing, verify you introduced no mojibake.\n"
                    "When a task says 'add X alongside Y, do not change Y,' the diff "
                    "must touch only the added region; if you cannot, stop and report "
                    "instead of reformatting."
                ),
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
                approval_flag="--permission-mode",
                approval_value="acceptEdits",
                output_format_flag="--output-format",
                output_format="text",
                model_flag="--model",
                prompt_suffix=(
                    "Make minimal, additive diffs. Do not reformat, re-quote, "
                    "re-order imports, or rewrite/translate comments on lines "
                    "unrelated to your change.\n"
                    "Never alter existing non-ASCII (e.g. Japanese) text. Preserve "
                    "existing comments byte-for-byte unless the task is specifically "
                    "to change them. After editing, verify you introduced no mojibake.\n"
                    "When a task says 'add X alongside Y, do not change Y,' the diff "
                    "must touch only the added region; if you cannot, stop and report "
                    "instead of reformatting."
                ),
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
    config_cwd = Path(cwd)
    load_dotenv(config_cwd)
    machine_path = resolve_machine_config_path()
    raw_config, repo_config_source = _load_raw_config(config_cwd, machine_path)
    api_url = str(
        os.getenv("ISSUEKIT_API_URL", raw_config.get("api_url", IssuekitConfig.api_url))
    ).strip()
    worker = _load_worker(raw_config.get("worker"))
    configured_project = raw_config.get("project", _SENTINEL)
    project = str(
        os.getenv(
            "ISSUEKIT_PROJECT",
            (
                configured_project
                if configured_project is not _SENTINEL
                else worker.repo_id
                if worker is not None
                else IssuekitConfig.project
            ),
        )
    ).strip()
    _validate_project(project)
    disabled_agents = _load_disabled_agents(
        raw_config.get("disabled_agents", IssuekitConfig.disabled_agents)
    )
    agents, agent_policies = _load_agents(raw_config.get("agents", {}))
    agents = _filter_disabled_agents(agents, disabled_agents)
    agent_policies = _filter_disabled_agent_policies(agent_policies, disabled_agents)
    assignees = _load_assignees(raw_config, agents, disabled_agents)
    default_reviewer = (
        "auto"
        if api_url
        else str(raw_config.get("default_reviewer", IssuekitConfig.default_reviewer)).strip()
    )
    _validate_not_disabled("default_reviewer", default_reviewer, disabled_agents)
    _validate_default_reviewer(default_reviewer, assignees)
    work_branch = str(raw_config.get("work_branch", IssuekitConfig.work_branch)).strip()
    _validate_work_branch(work_branch)
    claim_sync_interval_sec = float(
        raw_config.get(
            "claim_sync_interval_sec",
            IssuekitConfig.claim_sync_interval_sec,
        )
    )
    _validate_claim_sync_interval(claim_sync_interval_sec)
    triage = _load_triage_policy(raw_config.get("triage", {}))
    router = _load_router_policy(raw_config.get("router", {}))
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
        project=project,
        api_timeout=float(
            os.getenv("ISSUEKIT_API_TIMEOUT", raw_config.get("api_timeout", IssuekitConfig.api_timeout))
        ),
        ascii_id_threshold=int(
            raw_config.get("ascii_id_threshold", IssuekitConfig.ascii_id_threshold)
        ),
        issues_dir=str(raw_config.get("issues_dir", IssuekitConfig.issues_dir)),
        assignees=assignees,
        stages=_string_tuple(raw_config.get("stages", IssuekitConfig.stages)),
        default_reviewer=default_reviewer,
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
        disabled_agents=disabled_agents,
        machine_config_path=machine_path if machine_path is not None and machine_path.is_file() else None,
        repo_config_source=repo_config_source,
        agents=agents,
        agent_policies=agent_policies,
    )


def has_local_project_context(cwd: Path | str = ".") -> bool:
    """Return true when cwd looks like an issuekit project root."""

    config_cwd = Path(cwd)
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
    config_home = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "issuekit" / "config.toml"


def _load_raw_config(cwd: Path, machine_path: Path | None) -> tuple[dict[str, object], str]:
    raw_config = _load_machine_config(machine_path)
    repo_config_source = "none"
    pyproject_path = cwd / "pyproject.toml"
    if pyproject_path.exists():
        data = _load_config_toml(pyproject_path)
        pyproject_config = data.get("tool", {}).get("issuekit")
        if pyproject_config is not None:
            repo_config_source = "pyproject [tool.issuekit]"
            # pyproject's [tool.issuekit] wins when present so Python repos keep
            # their existing behavior even if a standalone config also exists.
            raw_config = _merge_config_layers(raw_config, dict(pyproject_config))

    issuekit_path = cwd / "issuekit.toml"
    if repo_config_source == "none" and issuekit_path.exists():
        repo_config_source = "issuekit.toml"
        raw_config = _merge_config_layers(raw_config, _load_config_toml(issuekit_path))

    return _merge_local_config(cwd, raw_config), repo_config_source


def _load_machine_config(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}
    config = _load_config_toml(path)
    if "worker" in config:
        raise ValueError(
            f"Machine config {path} cannot define worker; use issuekit.local.toml"
        )
    return config


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
    return tuple(str(value).split()) if isinstance(value, str) else tuple()


def _load_disabled_agents(value: object) -> tuple[str, ...]:
    disabled_agents = _dedupe_tokens(_string_tuple(value))
    for agent in disabled_agents:
        if not agent or not is_valid_workflow_token(agent):
            raise ValueError(f"Invalid disabled_agents token: {agent}")
    return disabled_agents


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


def _validate_project(project: str) -> None:
    if not project or not is_valid_workflow_token(project):
        raise ValueError(f"Invalid project token: {project}")


def _validate_work_branch(work_branch: str) -> None:
    if not work_branch:
        return
    if has_non_ascii(work_branch) or any(char.isspace() for char in work_branch):
        raise ValueError(f"Invalid work_branch token: {work_branch}")


def _validate_claim_sync_interval(value: float) -> None:
    if value < 0:
        raise ValueError("claim_sync_interval_sec must be zero or greater.")


def _load_agents(
    raw: dict[str, object],
) -> tuple[tuple[tuple[str, AgentRunConfig], ...], tuple[tuple[str, AgentPolicy], ...]]:
    if not raw:
        return IssuekitConfig.agents, IssuekitConfig.agent_policies
    default_agents = IssuekitConfig.agents
    default_by_name = dict(default_agents)
    default_policies = dict(IssuekitConfig.agent_policies)
    configured: dict[str, AgentRunConfig] = {}
    configured_policies: dict[str, AgentPolicy] = {}
    new_agent_names: list[str] = []
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        base = default_by_name.get(name, AgentRunConfig(binary=name))
        configured[name] = replace(base, **_agent_run_config_overrides(cfg))
        policy = default_policies.get(name, AgentPolicy())
        configured_policies[name] = replace(policy, **_agent_policy_overrides(cfg))
        if configured[name].reasoning_effort and not configured[name].effort_argv:
            raise ValueError(
                f"agents.{name}.reasoning_effort requires agents.{name}.effort_argv."
            )
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
    return tuple(result), policies


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
    max_adoptions = int(
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
    max_targets = int(raw.get("max_targets", RouterPolicy.max_targets))
    if max_targets < 1:
        raise ValueError("router.max_targets must be greater than zero.")
    max_clarify_rounds = int(
        raw.get("max_clarify_rounds", RouterPolicy.max_clarify_rounds)
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
        "known_paths": _string_tuple,
        "headless_argv": _string_tuple,
        "resumable": _bool_value,
        "session_flag": optional_str,
        "approval_flag": optional_str,
        "approval_value": optional_str,
        "output_format_flag": optional_str,
        "output_format": optional_str,
        "model_flag": optional_str,
        "model": optional_str,
        "reasoning_effort": optional_str,
        "effort_argv": _string_tuple,
        "prompt_suffix": optional_str,
        "model_prompts": _model_prompts,
    }
    overrides: dict[str, object] = {}
    for key, loader in loaders.items():
        value = cfg.get(key, _SENTINEL)
        if value is not _SENTINEL:
            overrides[key] = loader(value)
    return overrides


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
