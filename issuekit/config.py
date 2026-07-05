"""Configuration loading for issuekit."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from pathlib import Path

from issuekit.core import (
    VALID_ISSUE_PRIORITIES,
    has_non_ascii,
    is_valid_workflow_token,
    optional_int,
    optional_str,
)
from issuekit.dotenv import load_dotenv
from issuekit.localconfig import LocalConfigError, load_toml, read_local_config
from issuekit.worker_keys import legacy_worker_key, worker_key


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
class AgentRunConfig:
    """Per-agent headless run settings."""

    binary: str
    known_paths: tuple[str, ...] = ()
    headless_argv: tuple[str, ...] = ()
    resumable: bool = False
    session_flag: str | None = None
    approval_flag: str | None = None
    approval_value: str | None = None
    output_format_flag: str | None = None
    output_format: str | None = None
    model_flag: str | None = None
    model: str | None = None
    prompt_suffix: str | None = None
    model_prompts: tuple[tuple[str, str], ...] = ()
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
    stages: tuple[str, ...] = ("todo", "implementing", "review", "changes_requested", "done")
    default_reviewer: str = "claude"
    require_distinct_reviewer: bool = False
    work_branch: str = ""
    worker: WorkerIdentity | None = None
    worker_role: str = ""
    worker_description: str = ""
    repo_description: str = ""
    repo_metadata: dict[str, str] = field(default_factory=dict)
    worker_metadata: dict[str, str] = field(default_factory=dict)
    profile_file: str = DEFAULT_PROFILE_FILE
    profile_summary: str = ""
    profile_tags: tuple[str, ...] = ()
    triage: TriagePolicy = field(default_factory=TriagePolicy)
    router: RouterPolicy = field(default_factory=RouterPolicy)
    agents: tuple[tuple[str, AgentRunConfig], ...] = (
        (
            "kimi",
            AgentRunConfig(
                binary="kimi",
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
                approval_flag="--full-auto",
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
                mojibake_gate=True,
                diff_shape_warn_deletions=40,
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
                mojibake_gate=True,
                diff_shape_warn_deletions=40,
            ),
        ),
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

    def legacy_worker_key(self) -> str | None:
        if self.worker is None:
            return None
        return legacy_worker_key(
            self.worker.machine_id,
            self.worker.repo_id,
            self.worker.worker_name,
        )

    def worker_lookup_keys(self) -> tuple[str, ...]:
        current = self.worker_key()
        legacy = self.legacy_worker_key()
        return tuple(key for key in (current, legacy) if key)


def load_config(cwd: Path | str = ".") -> IssuekitConfig:
    config_cwd = Path(cwd)
    load_dotenv(config_cwd)
    raw_config = _load_raw_config(config_cwd)
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
    assignees = _string_tuple(raw_config.get("assignees", IssuekitConfig.assignees))
    default_reviewer = (
        "auto"
        if api_url
        else str(raw_config.get("default_reviewer", IssuekitConfig.default_reviewer)).strip()
    )
    _validate_default_reviewer(default_reviewer, assignees)
    work_branch = str(raw_config.get("work_branch", IssuekitConfig.work_branch)).strip()
    _validate_work_branch(work_branch)
    agents = _load_agents(raw_config.get("agents", {}))
    triage = _load_triage_policy(raw_config.get("triage", {}))
    router = _load_router_policy(raw_config.get("router", {}))
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
        worker=worker,
        worker_role=worker_role,
        worker_description=worker_description,
        repo_description=repo_description,
        repo_metadata=repo_metadata,
        worker_metadata=worker_metadata,
        profile_file=profile_file,
        profile_summary=profile_summary,
        profile_tags=profile_tags,
        triage=triage,
        router=router,
        agents=agents,
    )


def _load_raw_config(cwd: Path) -> dict[str, object]:
    raw_config: dict[str, object] = {}
    pyproject_has_issuekit = False
    pyproject_path = cwd / "pyproject.toml"
    if pyproject_path.exists():
        data = _load_config_toml(pyproject_path)
        pyproject_config = data.get("tool", {}).get("issuekit")
        if pyproject_config is not None:
            pyproject_has_issuekit = True
            # pyproject's [tool.issuekit] wins when present so Python repos keep
            # their existing behavior even if a standalone config also exists.
            raw_config = dict(pyproject_config)

    issuekit_path = cwd / "issuekit.toml"
    if not pyproject_has_issuekit and issuekit_path.exists():
        raw_config = _load_config_toml(issuekit_path)

    return _merge_local_worker_config(cwd, raw_config)


def _merge_local_worker_config(cwd: Path, raw_config: dict[str, object]) -> dict[str, object]:
    try:
        local_worker = read_local_config(cwd).worker
    except LocalConfigError as exc:
        raise ValueError(str(exc)) from exc
    if local_worker is None:
        return raw_config
    merged = dict(raw_config)
    merged["worker"] = dict(local_worker)
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


def _load_agents(raw: dict[str, object]) -> tuple[tuple[str, AgentRunConfig], ...]:
    if not raw:
        return IssuekitConfig.agents
    default_agents = IssuekitConfig.agents
    default_by_name = dict(default_agents)
    configured: dict[str, AgentRunConfig] = {}
    new_agent_names: list[str] = []
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        base = default_by_name.get(name, AgentRunConfig(binary=name))
        configured[name] = replace(base, **_agent_overrides(cfg))
        if name not in default_by_name:
            new_agent_names.append(name)

    result: list[tuple[str, AgentRunConfig]] = []
    for name, default_config in default_agents:
        result.append((name, configured.get(name, default_config)))
    for name in new_agent_names:
        result.append((name, configured[name]))
    return tuple(result)


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


def _agent_overrides(cfg: dict[str, object]) -> dict[str, object]:
    loaders = {
        "binary": str,
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
        "prompt_suffix": optional_str,
        "model_prompts": _model_prompts,
        "mojibake_gate": _bool_value,
        "diff_shape_warn_deletions": optional_int,
    }
    overrides: dict[str, object] = {}
    for key, loader in loaders.items():
        value = cfg.get(key, _SENTINEL)
        if value is not _SENTINEL:
            overrides[key] = loader(value)
    return overrides


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
