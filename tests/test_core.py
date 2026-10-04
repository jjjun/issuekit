from pathlib import Path

import pytest

from issuekit import core
from issuekit.config import IssuekitConfig, load_config


def test_load_config_reads_tool_issuekit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ISSUEKIT_CONFIG", "")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nproject = 'custom-project'\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config == IssuekitConfig(
        project="custom-project",
        assignees=IssuekitConfig.assignees,
        stages=IssuekitConfig.stages,
    )


def test_parse_issue_id_arg() -> None:
    assert core.parse_issue_id_arg("42") == 42
    with pytest.raises(ValueError, match="Invalid issue id: not-a-number"):
        core.parse_issue_id_arg("not-a-number")


def test_worker_row_requires_worker_name() -> None:
    row = {"repo_id": "repo", "worker_id": "legacy-checkout"}

    assert core.worker_display_from_row(row) == "?.?"
    assert core.worker_keys_from_row(row) == set()
