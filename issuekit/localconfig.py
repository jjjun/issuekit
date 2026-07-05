"""Shared helpers for issuekit machine-local files."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import tomllib


LOCAL_CONFIG_NAME = "issuekit.local.toml"
LOCAL_GITIGNORE_ENTRIES = (LOCAL_CONFIG_NAME, ".agent-runs/")


class LocalConfigError(RuntimeError):
    """Raised when issuekit machine-local files cannot be loaded."""


@dataclass(frozen=True)
class LocalConfig:
    worker: dict[str, object] | None
    refs: dict[str, str]
    author_guard: dict[str, object] | None


_PRESERVE = object()


def load_toml(path: Path) -> dict[str, object]:
    try:
        return dict(tomllib.loads(path.read_text(encoding="utf-8-sig")))
    except tomllib.TOMLDecodeError as exc:
        raise LocalConfigError(f"Failed to parse {path}: {exc}") from exc


def read_local_config(cwd: Path | str = ".") -> LocalConfig:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    if not path.exists():
        return LocalConfig(worker=None, refs={}, author_guard=None)
    data = load_toml(path)
    refs = data.get("refs", {})
    if not isinstance(refs, dict):
        raise LocalConfigError(f"{LOCAL_CONFIG_NAME} must contain a [refs] table.")
    return LocalConfig(
        worker=_worker_table(data),
        refs={str(name): str(value) for name, value in refs.items()},
        author_guard=_author_guard_table(data),
    )


def write_local_config(
    cwd: Path | str = ".",
    *,
    worker: Mapping[str, object] | None,
    refs: Mapping[str, str],
    author_guard: Mapping[str, object] | None | object = _PRESERVE,
) -> None:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    if author_guard is _PRESERVE:
        author_guard = read_local_config(cwd).author_guard if path.exists() else None
    path.write_text(
        local_config_text(worker=worker, refs=refs, author_guard=author_guard),
        encoding="utf-8",
        newline="\n",
    )


def local_config_text(
    *,
    worker: Mapping[str, object] | None,
    refs: Mapping[str, str],
    author_guard: Mapping[str, object] | None = None,
) -> str:
    return _local_config_text(worker=worker, refs=refs, author_guard=author_guard)


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
    author_guard: Mapping[str, object] | None,
) -> str:
    lines: list[str] = []
    if worker:
        lines.append("[worker]")
        for key in ("machine_id", "repo_id", "worker_name"):
            value = worker.get(key)
            if key == "worker_name" and value is None:
                value = worker.get("worker_id")
            if value is not None:
                lines.append(f"{key} = {json.dumps(str(value))}")
        lines.append("")
    if author_guard:
        lines.append("[author_guard]")
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


def _author_guard_table(data: dict[str, object]) -> dict[str, object] | None:
    guard = data.get("author_guard")
    return guard if isinstance(guard, dict) else None
