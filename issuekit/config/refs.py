"""Machine-local related repository refs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from issuekit.core import is_valid_workflow_token
from .local import (
    LOCAL_CONFIG_NAME,
    LocalConfigError,
    load_toml,
    read_local_config,
    write_local_config,
)


WORKSPACE_CONFIG_NAME = "issuekit.workspace.toml"
WORKSPACE_ENV_VAR = "ISSUEKIT_WORKSPACE"


class RefError(RuntimeError):
    """Raised when a repository ref cannot be loaded or resolved."""


@dataclass(frozen=True)
class RefEntry:
    path: Path
    source: str


def load_refs(cwd: Path | str = ".") -> dict[str, str]:
    return _read_local_config(cwd).refs


def find_workspace_file(cwd: Path | str = ".") -> Path | None:
    env_path = os.environ.get(WORKSPACE_ENV_VAR, "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_absolute():
            path = Path(cwd) / path
        path = path.resolve()
        if not path.exists():
            raise RefError(f"{WORKSPACE_ENV_VAR} points to a missing file: {path}")
        if not path.is_file():
            raise RefError(f"{WORKSPACE_ENV_VAR} must point to a file: {path}")
        return path

    start = Path(cwd).resolve()
    if start.is_file():
        start = start.parent
    for current in (start, *start.parents):
        candidate = current / WORKSPACE_CONFIG_NAME
        if candidate.exists():
            if not candidate.is_file():
                raise RefError(f"Workspace config is not a file: {candidate}")
            return candidate
    return None


def load_workspace_refs(cwd: Path | str = ".") -> dict[str, str]:
    workspace_file = find_workspace_file(cwd)
    if workspace_file is None:
        return {}
    return _load_workspace_refs_from_file(workspace_file)


def load_effective_refs(cwd: Path | str = ".") -> dict[str, RefEntry]:
    effective: dict[str, RefEntry] = {
        name: RefEntry(path=Path(path), source="workspace")
        for name, path in load_workspace_refs(cwd).items()
    }
    effective.update(
        {
            name: RefEntry(path=Path(path), source="local")
            for name, path in _normalized_local_refs(cwd).items()
        }
    )
    return dict(sorted(effective.items()))


def save_refs(refs: dict[str, str], cwd: Path | str = ".") -> None:
    local_config = _read_local_config(cwd)
    write_local_config(cwd, worker=local_config.worker, refs=refs)


def add_ref(name: str, repo_path: Path | str, cwd: Path | str = ".") -> dict[str, str]:
    _validate_ref_name(name)
    resolved_path = Path(repo_path).expanduser().resolve()
    if not resolved_path.exists():
        raise RefError(f"Ref target path does not exist: {resolved_path}")
    if not resolved_path.is_dir():
        raise RefError(f"Ref target path is not a directory: {resolved_path}")
    refs = load_refs(cwd)
    refs[name] = resolved_path.as_posix()
    save_refs(refs, cwd)
    return refs


def add_workspace_ref(
    name: str,
    repo_path: Path | str,
    cwd: Path | str = ".",
    *,
    workspace_path: Path | str | None = None,
) -> dict[str, str]:
    _validate_ref_name(name)
    resolved_path = Path(repo_path).expanduser().resolve()
    if not resolved_path.exists():
        raise RefError(f"Ref target path does not exist: {resolved_path}")
    if not resolved_path.is_dir():
        raise RefError(f"Ref target path is not a directory: {resolved_path}")

    workspace_file = _workspace_file_for_write(cwd, workspace_path)
    refs = _load_workspace_raw_refs(workspace_file)
    refs[name] = _workspace_storage_value(resolved_path, workspace_file.parent)
    save_workspace_refs(refs, workspace_file)
    return refs


def save_workspace_refs(refs: dict[str, str], workspace_file: Path | str) -> None:
    path = Path(workspace_file)
    lines = ["[projects]"]
    for name in sorted(refs):
        lines.append(f"{name} = {json.dumps(refs[name])}")
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def list_effective_refs(cwd: Path | str = ".") -> dict[str, RefEntry]:
    return load_effective_refs(cwd)


def default_repo_ref(cwd: Path | str = ".") -> str:
    name = Path(cwd).resolve().name
    return name if is_valid_workflow_token(name) else _slug_token(name)


def current_repo_ref(cwd: Path | str = ".") -> str:
    current = Path(cwd).resolve()
    refs = load_effective_refs(cwd)
    for source in ("workspace", "local"):
        matches = [
            name
            for name, entry in refs.items()
            if entry.source == source and entry.path.resolve() == current
        ]
        if matches:
            return sorted(matches)[0]
    return default_repo_ref(cwd)


def _validate_ref_name(name: str) -> None:
    if not is_valid_workflow_token(name):
        raise RefError(f"Invalid ref name: {name}")


def _normalized_local_refs(cwd: Path | str = ".") -> dict[str, str]:
    refs: dict[str, str] = {}
    base = Path(cwd).resolve()
    for name, value in load_refs(cwd).items():
        _validate_ref_name(name)
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        refs[name] = path.resolve().as_posix()
    return refs


def _load_workspace_refs_from_file(workspace_file: Path) -> dict[str, str]:
    refs = _load_workspace_raw_refs(workspace_file)
    base = workspace_file.parent
    resolved: dict[str, str] = {}
    for name, value in refs.items():
        _validate_ref_name(name)
        target = Path(value).expanduser()
        if not target.is_absolute():
            target = base / target
        resolved[name] = target.resolve().as_posix()
    return resolved


def _load_workspace_raw_refs(workspace_file: Path) -> dict[str, str]:
    try:
        data = load_toml(workspace_file)
    except LocalConfigError as exc:
        raise RefError(str(exc)) from exc
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise RefError(f"{WORKSPACE_CONFIG_NAME} must contain a [projects] table.")
    refs: dict[str, str] = {}
    for name, value in projects.items():
        if not isinstance(value, str):
            raise RefError(f"Workspace ref {name} must be a string path.")
        refs[str(name)] = value
    return refs


def _workspace_file_for_write(
    cwd: Path | str,
    workspace_path: Path | str | None,
) -> Path:
    if workspace_path is not None:
        path = Path(workspace_path).expanduser()
        if not path.is_absolute():
            path = Path(cwd) / path
        path = path.resolve()
        if path.exists() and not path.is_file():
            raise RefError(f"Workspace config is not a file: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            save_workspace_refs({}, path)
        return path

    workspace_file = find_workspace_file(cwd)
    if workspace_file is None:
        raise RefError(
            "No issuekit.workspace.toml found. Create one above this repo or pass "
            "--path-to-workspace."
        )
    return workspace_file


def _workspace_storage_value(repo_path: Path, workspace_dir: Path) -> str:
    try:
        return repo_path.relative_to(workspace_dir).as_posix() or "."
    except ValueError:
        return repo_path.as_posix()


def _slug_token(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    token = "-".join(part for part in token.split("-") if part)
    if not token:
        return "repo"
    if not token[0].isalnum():
        token = f"repo-{token}"
    return token[:32]


def _read_local_config(cwd: Path | str = "."):
    try:
        return read_local_config(cwd)
    except LocalConfigError as exc:
        raise RefError(str(exc)) from exc
