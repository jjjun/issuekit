from pathlib import Path

import pytest

from issuekit.config import IssuekitConfig
from issuekit.guards.branch import enforce_work_branch
from issuekit.workflow import WorkflowError


def test_work_branch_guard_is_disabled_when_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")

    enforce_work_branch(tmp_path, config=IssuekitConfig(), action="claim-next")


def test_work_branch_guard_allows_matching_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "main")

    enforce_work_branch(tmp_path, config=IssuekitConfig(work_branch="main"), action="claim-next")


def test_work_branch_guard_blocks_different_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")

    with pytest.raises(WorkflowError) as excinfo:
        enforce_work_branch(
            tmp_path,
            config=IssuekitConfig(work_branch="main"),
            action="claim-next",
        )

    message = str(excinfo.value)
    assert "Work-branch guard blocks claim-next" in message
    assert "checkout is on branch 'feature'" in message
    assert "work_branch is 'main'" in message
    assert excinfo.value.code == "work_branch_guard"


def test_work_branch_guard_fails_closed_when_branch_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: None)

    with pytest.raises(WorkflowError, match="checkout branch could not be determined"):
        enforce_work_branch(
            tmp_path,
            config=IssuekitConfig(work_branch="main"),
            action="submit issue #1 for review",
        )


def test_work_branch_guard_allows_explicit_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")

    enforce_work_branch(
        tmp_path,
        config=IssuekitConfig(work_branch="main"),
        action="claim-next",
        allow_any_branch=True,
    )
