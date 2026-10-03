"""Repository root resolution for configuration and repository-wide commands."""

from __future__ import annotations

from pathlib import Path

from issuekit.config.local import LocalConfigError, load_toml
from issuekit.gitutil import git_root


def resolve_repository_root(cwd: Path | str = ".") -> Path:
    """Return the nearest config root in cwd's repo, or the git root."""

    path = Path(cwd).resolve()
    if not any((parent / ".git").exists() for parent in (path, *path.parents)):
        return path
    repository_root = git_root(path)
    if repository_root is None:
        return path
    if repository_root not in (path, *path.parents):
        return path

    candidate_root = path
    while True:
        if has_config_candidate(candidate_root):
            return candidate_root
        if candidate_root == repository_root or candidate_root.parent == candidate_root:
            return repository_root
        candidate_root = candidate_root.parent


def has_config_candidate(root: Path | str) -> bool:
    """Return whether root contains an issuekit repo config candidate."""

    root = Path(root)
    if (root / "issuekit.toml").exists() or (root / "issuekit.local.toml").exists():
        return True
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.exists():
        return False
    try:
        data = load_toml(pyproject_path)
    except LocalConfigError:
        return True
    tool_config = data.get("tool")
    return isinstance(tool_config, dict) and isinstance(tool_config.get("issuekit"), dict)
