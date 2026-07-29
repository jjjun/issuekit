"""Tests for local project profile loading."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuekit.config import IssuekitConfig, load_config
from issuekit.config.project_profile import (
    PROFILE_MD_MAX_BYTES,
    ProjectProfile,
    load_project_profile,
)


def _config(tmp_path: Path, extra: str = "") -> IssuekitConfig:
    (tmp_path / "issuekit.toml").write_text(
        ("project = 'issuekit'\n" + extra),
        encoding="utf-8",
        newline="\n",
    )
    return load_config(tmp_path)


def test_load_project_profile_absent_returns_none(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert load_project_profile(config, tmp_path) is None


def test_load_project_profile_reads_file_and_config_metadata(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        "profile_summary = 'Short summary.'\nprofile_tags = ['python', 'cli']\n",
    )
    (tmp_path / "ISSUEKIT.md").write_text(
        "# Profile\n\nResponsibilities.\n", encoding="utf-8", newline="\n"
    )

    profile = load_project_profile(config, tmp_path)

    assert isinstance(profile, ProjectProfile)
    assert profile.profile_md == "# Profile\n\nResponsibilities.\n"
    assert profile.summary == "Short summary."
    assert profile.tags == ("python", "cli")
    # No git repo -> metadata is empty, not an error.
    assert profile.source_commit == ""
    assert profile.source_committed_at == ""
    assert profile.to_payload() == {
        "summary": "Short summary.",
        "profile_md": "# Profile\n\nResponsibilities.\n",
        "tags": ["python", "cli"],
    }


def test_load_project_profile_honors_custom_profile_file(tmp_path: Path) -> None:
    config = _config(tmp_path, "profile_file = 'PROFILE.md'\n")
    (tmp_path / "PROFILE.md").write_text("custom\n", encoding="utf-8", newline="\n")

    profile = load_project_profile(config, tmp_path)

    assert profile is not None
    assert profile.profile_md == "custom\n"


def test_load_project_profile_rejects_oversized_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / "ISSUEKIT.md").write_text(
        "x" * (PROFILE_MD_MAX_BYTES + 1), encoding="utf-8", newline="\n"
    )

    with pytest.raises(ValueError, match="project profile limit"):
        load_project_profile(config, tmp_path)


def test_load_project_profile_reads_git_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / "ISSUEKIT.md").write_text("# Profile\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "ISSUEKIT.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add profile"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
    )

    profile = load_project_profile(config, tmp_path)

    assert profile is not None
    assert len(profile.source_commit) == 40
    assert profile.source_committed_at  # ISO 8601 commit date
