"""Configuration loading for issuekit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class IssuekitConfig:
    recent_count: int = 30
    ascii_id_threshold: int = 0
    issues_dir: str = "docs/issues"
    assignees: tuple[str, ...] = ("codex", "claude")
    stages: tuple[str, ...] = ("todo", "implementing", "review", "changes_requested", "done")

    def issues_path(self, cwd: Path | str = ".") -> Path:
        path = Path(self.issues_dir)
        if path.is_absolute():
            return path
        return Path(cwd) / path


def load_config(cwd: Path | str = ".") -> IssuekitConfig:
    pyproject_path = Path(cwd) / "pyproject.toml"
    if not pyproject_path.exists():
        return IssuekitConfig()

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8-sig"))
    raw_config = data.get("tool", {}).get("issuekit", {})
    return IssuekitConfig(
        recent_count=int(raw_config.get("recent_count", IssuekitConfig.recent_count)),
        ascii_id_threshold=int(
            raw_config.get("ascii_id_threshold", IssuekitConfig.ascii_id_threshold)
        ),
        issues_dir=str(raw_config.get("issues_dir", IssuekitConfig.issues_dir)),
        assignees=_string_tuple(raw_config.get("assignees", IssuekitConfig.assignees)),
        stages=_string_tuple(raw_config.get("stages", IssuekitConfig.stages)),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return tuple(str(value).split()) if isinstance(value, str) else tuple()
