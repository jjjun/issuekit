"""Configuration loading for issuekit."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import tomllib

from issuekit.core import is_valid_workflow_token
from issuekit.dotenv import load_dotenv


_SENTINEL = object()


@dataclass(frozen=True)
class AgentRunConfig:
    """Per-agent headless run settings."""

    binary: str
    known_paths: tuple[str, ...] = ()
    headless_argv: tuple[str, ...] = ()
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
class IssuekitConfig:
    api_url: str = ""
    project: str = "issuekit"
    api_timeout: float = 30.0
    recent_count: int = 30
    ascii_id_threshold: int = 0
    issues_dir: str = "docs/issues"
    assignees: tuple[str, ...] = ("codex", "claude", "kimi")
    stages: tuple[str, ...] = ("todo", "implementing", "review", "changes_requested", "done")
    default_reviewer: str = "claude"
    require_distinct_reviewer: bool = False
    require_review_before_complete: bool = True
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


def load_config(cwd: Path | str = ".") -> IssuekitConfig:
    config_cwd = Path(cwd)
    load_dotenv(config_cwd)
    raw_config = _load_raw_config(config_cwd)
    api_url = str(
        os.getenv("ISSUEKIT_API_URL", raw_config.get("api_url", IssuekitConfig.api_url))
    ).strip()
    project = str(
        os.getenv("ISSUEKIT_PROJECT", raw_config.get("project", IssuekitConfig.project))
    ).strip()
    _validate_project(project)
    assignees = _string_tuple(raw_config.get("assignees", IssuekitConfig.assignees))
    default_reviewer = (
        "auto"
        if api_url
        else str(raw_config.get("default_reviewer", IssuekitConfig.default_reviewer)).strip()
    )
    _validate_default_reviewer(default_reviewer, assignees)
    agents = _load_agents(raw_config.get("agents", {}))
    return IssuekitConfig(
        api_url=api_url,
        project=project,
        api_timeout=float(
            os.getenv("ISSUEKIT_API_TIMEOUT", raw_config.get("api_timeout", IssuekitConfig.api_timeout))
        ),
        recent_count=int(raw_config.get("recent_count", IssuekitConfig.recent_count)),
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
        require_review_before_complete=_bool_value(
            raw_config.get(
                "require_review_before_complete",
                IssuekitConfig.require_review_before_complete,
            )
        ),
        agents=agents,
    )


def _load_raw_config(cwd: Path) -> dict[str, object]:
    pyproject_path = cwd / "pyproject.toml"
    if pyproject_path.exists():
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8-sig"))
        raw_config = data.get("tool", {}).get("issuekit")
        if raw_config is not None:
            # pyproject's [tool.issuekit] wins when present so Python repos keep
            # their existing behavior even if a standalone config also exists.
            return dict(raw_config)

    issuekit_path = cwd / "issuekit.toml"
    if issuekit_path.exists():
        try:
            return dict(tomllib.loads(issuekit_path.read_text(encoding="utf-8-sig")))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Failed to parse {issuekit_path}: {exc}") from exc

    return {}


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


def _agent_overrides(cfg: dict[str, object]) -> dict[str, object]:
    loaders = {
        "binary": str,
        "known_paths": _string_tuple,
        "headless_argv": _string_tuple,
        "approval_flag": _optional_str,
        "approval_value": _optional_str,
        "output_format_flag": _optional_str,
        "output_format": _optional_str,
        "model_flag": _optional_str,
        "model": _optional_str,
        "prompt_suffix": _optional_str,
        "model_prompts": _model_prompts,
        "mojibake_gate": _bool_value,
        "diff_shape_warn_deletions": _optional_int,
    }
    overrides: dict[str, object] = {}
    for key, loader in loaders.items():
        value = cfg.get(key, _SENTINEL)
        if value is not _SENTINEL:
            overrides[key] = loader(value)
    return overrides


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _model_prompts(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(model), str(prompt)) for model, prompt in value.items())


def _bool_value(value: object) -> bool:
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
