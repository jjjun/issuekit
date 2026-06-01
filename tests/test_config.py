from pathlib import Path

import pytest

from issuekit.config import IssuekitConfig, load_config


def test_load_config_reads_standalone_issuekit_toml(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 407\nissues_dir = 'docs/issues'\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.ascii_id_threshold == 407
    assert config.issues_dir == "docs/issues"


def test_load_config_prefers_pyproject_tool_issuekit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nascii_id_threshold = 100\nissues_dir = 'py/issues'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 407\nissues_dir = 'standalone/issues'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        ascii_id_threshold=100,
        issues_dir="py/issues",
    )


def test_load_config_uses_issuekit_toml_when_pyproject_has_no_issuekit_table(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'example'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 407\nissues_dir = 'standalone/issues'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        ascii_id_threshold=407,
        issues_dir="standalone/issues",
    )


def test_load_config_uses_defaults_without_config_files(tmp_path: Path) -> None:
    assert load_config(tmp_path) == IssuekitConfig()


def test_load_config_malformed_issuekit_toml_names_file(tmp_path: Path) -> None:
    issuekit_path = tmp_path / "issuekit.toml"
    issuekit_path.write_text(
        "ascii_id_threshold = [\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match=r"issuekit\.toml"):
        load_config(tmp_path)


def test_load_config_reads_workflow_sets_from_issuekit_toml(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "assignees = ['alice', 'bob']\nstages = ['draft', 'review']\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice", "bob"),
        stages=("draft", "review"),
    )
