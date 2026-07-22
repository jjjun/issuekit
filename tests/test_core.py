from pathlib import Path

import pytest

from issuekit import core
from issuekit.config import IssuekitConfig, load_config


def test_valid_issue_statuses_match_server_derived_labels() -> None:
    assert core.VALID_ISSUE_STATUSES == {
        "active",
        "planned",
        "in_progress",
        "completed",
    }


def test_load_config_reads_tool_issuekit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nascii_id_threshold = 100\nissues_dir = 'custom/issues'\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config == IssuekitConfig(
        ascii_id_threshold=100,
        issues_dir="custom/issues",
        assignees=IssuekitConfig.assignees,
        stages=IssuekitConfig.stages,
    )
    assert config.issues_path(tmp_path) == tmp_path / "custom" / "issues"


def test_parse_issue_id_arg() -> None:
    assert core.parse_issue_id_arg("42") == 42
    with pytest.raises(ValueError, match="Invalid issue id: not-a-number"):
        core.parse_issue_id_arg("not-a-number")
