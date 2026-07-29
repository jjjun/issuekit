"""Load a committed project capability profile (ISSUEKIT.md + config metadata).

The long-form profile is a committed markdown file at the repo root (default
``ISSUEKIT.md``); ``profile_summary`` and ``profile_tags`` in ``[tool.issuekit]``
add short structured metadata. This is the client side of mine-py#172's
project-level ``ProjectProfile``; the push is best-effort and this module only
reads local state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from issuekit.config.settings import IssuekitConfig
from issuekit.core import drop_none
from issuekit.gitutil import run_git

PROFILE_MD_MAX_BYTES = 16 * 1024


@dataclass(frozen=True)
class ProjectProfile:
    """A loaded project profile ready to push or display."""

    profile_md: str
    summary: str = ""
    tags: tuple[str, ...] = ()
    source_commit: str = ""
    source_committed_at: str = ""

    def to_payload(self) -> dict[str, object]:
        return drop_none(
            {
                "summary": self.summary or None,
                "profile_md": self.profile_md,
                "tags": list(self.tags) or None,
                "source_commit": self.source_commit or None,
                "source_committed_at": self.source_committed_at or None,
            }
        )


def load_project_profile(config: IssuekitConfig, cwd: Path | str) -> ProjectProfile | None:
    """Return the local project profile, or None when no profile file exists.

    Raises ValueError when the profile file exceeds ``PROFILE_MD_MAX_BYTES``.
    """

    root = Path(cwd)
    profile_path = root / config.profile_file
    if not profile_path.is_file():
        return None

    raw = profile_path.read_bytes()
    if len(raw) > PROFILE_MD_MAX_BYTES:
        raise ValueError(
            f"{config.profile_file} is {len(raw)} bytes, over the "
            f"{PROFILE_MD_MAX_BYTES}-byte project profile limit; trim it or move "
            "detail into linked docs."
        )
    profile_md = raw.decode("utf-8", errors="replace")
    source_commit, source_committed_at = _git_profile_metadata(root, config.profile_file)
    return ProjectProfile(
        profile_md=profile_md,
        summary=config.profile_summary,
        tags=tuple(config.profile_tags),
        source_commit=source_commit,
        source_committed_at=source_committed_at,
    )


def _git_profile_metadata(root: Path, profile_file: str) -> tuple[str, str]:
    result = run_git(
        ["log", "-1", "--format=%H %cI", "--", profile_file],
        root,
        timeout=5,
    )
    if result is None or result.returncode != 0:
        return "", ""
    line = result.stdout.strip()
    if not line:
        return "", ""
    parts = line.split(" ", 1)
    commit = parts[0]
    committed_at = parts[1].strip() if len(parts) > 1 else ""
    return commit, committed_at
