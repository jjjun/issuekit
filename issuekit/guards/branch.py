"""Checkout branch guard for handoff lifecycle commands."""

from __future__ import annotations

from pathlib import Path

from issuekit.config import IssuekitConfig
from issuekit.errors import WorkflowError
from issuekit.gitutil import git_current_branch


def enforce_work_branch(
    cwd: Path | str,
    *,
    config: IssuekitConfig,
    action: str,
    allow_any_branch: bool = False,
) -> None:
    if allow_any_branch:
        return
    if not config.work_branch:
        return

    current = git_current_branch(cwd)
    if current is None:
        raise WorkflowError(
            "Work-branch guard blocks "
            f"{action}: checkout branch could not be determined, but work_branch "
            f"is '{config.work_branch}'. Switch to '{config.work_branch}' or update config.",
            code="work_branch_guard",
        )
    if current == config.work_branch:
        return

    raise WorkflowError(
        "Work-branch guard blocks "
        f"{action}: checkout is on branch '{current}' but work_branch is "
        f"'{config.work_branch}'. Switch to '{config.work_branch}' or update config.",
        code="work_branch_guard",
    )
