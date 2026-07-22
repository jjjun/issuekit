from pathlib import Path

from issuekit import core
from issuekit.config import IssuekitConfig, load_config


def test_workflow_token_shape_rejects_frontmatter_injection() -> None:
    assert core.is_valid_workflow_token("")
    assert core.is_valid_workflow_token("codex")
    assert not core.is_valid_workflow_token("codex\nstatus: completed")
    assert not core.is_valid_workflow_token("review:done")
    assert not core.is_valid_workflow_token("bad value")
    assert not core.is_valid_workflow_token("-codex")


def test_load_config_reads_workflow_sets(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nassignees = ['alice']\nstages = ['draft']\ndefault_reviewer = 'alice'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice",),
        stages=("draft",),
        default_reviewer="alice",
    )
