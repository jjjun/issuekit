"""Shared helpers for issuekit machine-local files."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

LOCAL_CONFIG_NAME = "issuekit.local.toml"
LOCAL_GITIGNORE_ENTRIES = (LOCAL_CONFIG_NAME, ".agent-runs/")


class LocalConfigError(RuntimeError):
    """Raised when issuekit machine-local files cannot be loaded."""


@dataclass(frozen=True)
class LocalConfig:
    worker: dict[str, object] | None
    disabled_agents: tuple[str, ...] | None
    refs: dict[str, str]
    author_guards: tuple[dict[str, object], ...]

    @property
    def author_guard(self) -> dict[str, object] | None:
        return self.author_guards[0] if self.author_guards else None


_PRESERVE = object()


def load_toml(path: Path) -> dict[str, object]:
    try:
        return dict(tomllib.loads(path.read_text(encoding="utf-8-sig")))
    except tomllib.TOMLDecodeError as exc:
        raise LocalConfigError(f"Failed to parse {path}: {exc}") from exc


def read_local_config(cwd: Path | str = ".") -> LocalConfig:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    if not path.exists():
        return LocalConfig(
            worker=None,
            disabled_agents=None,
            refs={},
            author_guards=(),
        )
    data = load_toml(path)
    refs = data.get("refs", {})
    if not isinstance(refs, dict):
        raise LocalConfigError(f"{LOCAL_CONFIG_NAME} must contain a [refs] table.")
    return LocalConfig(
        worker=_worker_table(data),
        disabled_agents=_disabled_agents(data),
        refs={str(name): str(value) for name, value in refs.items()},
        author_guards=_author_guard_tables(data),
    )


def write_local_config(
    cwd: Path | str = ".",
    *,
    worker: Mapping[str, object] | None,
    refs: Mapping[str, str],
    disabled_agents: tuple[str, ...] | None | object = _PRESERVE,
    author_guards: Sequence[Mapping[str, object]] | object = _PRESERVE,
) -> None:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    existing = read_local_config(cwd) if path.exists() else None
    if disabled_agents is _PRESERVE:
        disabled_agents = existing.disabled_agents if existing is not None else None
    if author_guards is _PRESERVE:
        author_guards = existing.author_guards if existing is not None else ()
    path.write_text(
        local_config_text(
            worker=worker,
            refs=refs,
            disabled_agents=disabled_agents,
            author_guards=author_guards,
        ),
        encoding="utf-8",
        newline="\n",
    )


def local_config_text(
    *,
    worker: Mapping[str, object] | None,
    refs: Mapping[str, str],
    disabled_agents: tuple[str, ...] | None = None,
    author_guards: Sequence[Mapping[str, object]] = (),
) -> str:
    return _local_config_text(
        worker=worker,
        refs=refs,
        disabled_agents=disabled_agents,
        author_guards=author_guards,
    )


def missing_gitignore_entries(content: str) -> list[str]:
    entries = {line.strip() for line in content.splitlines()}
    return [
        entry
        for entry in LOCAL_GITIGNORE_ENTRIES
        if entry not in entries and not (entry == ".agent-runs/" and ".agent-runs" in entries)
    ]


def ensure_gitignore_entries(cwd: Path | str = ".") -> bool:
    path = Path(cwd) / ".gitignore"
    if not path.exists():
        path.write_text(
            "".join(f"{entry}\n" for entry in LOCAL_GITIGNORE_ENTRIES),
            encoding="utf-8",
            newline="\n",
        )
        return True

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    missing_entries = missing_gitignore_entries(content)
    if not missing_entries:
        return False

    separator = "" if content.endswith("\n") or not content else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(separator)
        for entry in missing_entries:
            handle.write(f"{entry}\n")
    return True


def _local_config_text(
    *,
    worker: Mapping[str, object] | None,
    refs: Mapping[str, str],
    disabled_agents: tuple[str, ...] | None,
    author_guards: Sequence[Mapping[str, object]],
) -> str:
    lines: list[str] = []
    if disabled_agents is not None:
        lines.append(f"disabled_agents = {json.dumps(list(disabled_agents))}")
        lines.append("")
    if worker:
        lines.append("[worker]")
        for key in ("machine_id", "repo_id", "worker_name"):
            value = worker.get(key)
            if key == "worker_name" and value is None:
                value = worker.get("worker_id")
            if value is not None:
                lines.append(f"{key} = {json.dumps(str(value))}")
        lines.append("")
    for author_guard in author_guards:
        lines.append("[[author_guards]]")
        for key in (
            "project",
            "kind",
            "id",
            "ref",
            "target_project",
            "author_agent",
            "author_session",
            "worker",
            "created",
            "required_next_action",
        ):
            if key in author_guard:
                lines.append(f"{key} = {json.dumps(str(author_guard[key]))}")
        lines.append("")
    lines.append("[refs]")
    for name in sorted(refs):
        lines.append(f"{name} = {json.dumps(str(refs[name]))}")
    return "\n".join(lines) + "\n"


def _worker_table(data: dict[str, object]) -> dict[str, object] | None:
    worker = data.get("worker")
    if isinstance(worker, dict):
        return worker
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None
    issuekit = tool.get("issuekit")
    if not isinstance(issuekit, dict):
        return None
    worker = issuekit.get("worker")
    return worker if isinstance(worker, dict) else None


def _disabled_agents(data: dict[str, object]) -> tuple[str, ...] | None:
    if "disabled_agents" in data:
        return _string_tuple(data["disabled_agents"])
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None
    issuekit = tool.get("issuekit")
    if not isinstance(issuekit, dict) or "disabled_agents" not in issuekit:
        return None
    return _string_tuple(issuekit["disabled_agents"])


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return tuple(str(value).split()) if isinstance(value, str) else ()


def _author_guard_tables(data: dict[str, object]) -> tuple[dict[str, object], ...]:
    guards: list[dict[str, object]] = []
    legacy_guard = data.get("author_guard")
    if isinstance(legacy_guard, dict):
        guards.append(legacy_guard)
    author_guards = data.get("author_guards", [])
    if isinstance(author_guards, list):
        guards.extend(guard for guard in author_guards if isinstance(guard, dict))
    return tuple(guards)
