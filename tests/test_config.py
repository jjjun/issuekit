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


def test_default_assignees_includes_kimi() -> None:
    assert "kimi" in IssuekitConfig.assignees


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
        (
            "assignees = ['alice', 'bob']\n"
            "stages = ['draft', 'review']\n"
            "default_reviewer = 'bob'\n"
            "require_distinct_reviewer = true\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice", "bob"),
        stages=("draft", "review"),
        default_reviewer="bob",
        require_distinct_reviewer=True,
    )


def test_load_config_accepts_auto_default_reviewer(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "assignees = ['alice', 'bob']\ndefault_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice", "bob"),
        default_reviewer="auto",
    )


def test_load_config_coerces_string_distinct_reviewer_flag(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_distinct_reviewer = 'yes'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).require_distinct_reviewer is True


def test_load_config_rejects_invalid_default_reviewer_token(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'bad value'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid default_reviewer token"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_default_reviewer(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "assignees = ['alice', 'bob']\ndefault_reviewer = 'claude'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Unknown default_reviewer"):
        load_config(tmp_path)


def test_load_config_reads_require_review_before_complete(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_review_before_complete = false\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).require_review_before_complete is False


def test_load_config_defaults_require_review_before_complete_true(tmp_path: Path) -> None:
    assert load_config(tmp_path).require_review_before_complete is True
