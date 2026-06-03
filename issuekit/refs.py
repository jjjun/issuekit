"""Machine-local related repository refs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib

from issuekit.config import load_config
from issuekit.core import is_valid_workflow_token


LOCAL_CONFIG_NAME = "issuekit.local.toml"


class RefError(RuntimeError):
    """Raised when a repository ref cannot be loaded or resolved."""


@dataclass(frozen=True)
class RefResolution:
    name: str
    repo_path: Path
    issues_dir: Path


def load_refs(cwd: Path | str = ".") -> dict[str, str]:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise RefError(f"Failed to parse {path}: {exc}") from exc
    refs = data.get("refs", {})
    if not isinstance(refs, dict):
        raise RefError(f"{LOCAL_CONFIG_NAME} must contain a [refs] table.")
    return {str(name): str(value) for name, value in refs.items()}


def save_refs(refs: dict[str, str], cwd: Path | str = ".") -> None:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    lines = ["[refs]"]
    for name in sorted(refs):
        lines.append(f"{name} = {json.dumps(refs[name])}")
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


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


def list_refs(cwd: Path | str = ".") -> dict[str, str]:
    return dict(sorted(load_refs(cwd).items()))


def resolve_ref(name: str, cwd: Path | str = ".") -> RefResolution:
    refs = load_refs(cwd)
    if name not in refs:
        raise RefError(f"Unknown ref: {name}")
    repo_path = Path(refs[name])
    if not repo_path.exists():
        raise RefError(f"Ref target path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise RefError(f"Ref target path is not a directory: {repo_path}")
    config = load_config(repo_path)
    return RefResolution(name=name, repo_path=repo_path, issues_dir=config.issues_path(repo_path))


def default_repo_ref(cwd: Path | str = ".") -> str:
    name = Path(cwd).resolve().name
    return name if is_valid_workflow_token(name) else _slug_token(name)


def _validate_ref_name(name: str) -> None:
    if not is_valid_workflow_token(name):
        raise RefError(f"Invalid ref name: {name}")


def _slug_token(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    token = "-".join(part for part in token.split("-") if part)
    if not token:
        return "repo"
    if not token[0].isalnum():
        token = f"repo-{token}"
    return token[:32]
