"""Claim-time checkout cleanliness and fast-forward sync guard."""

from __future__ import annotations

from pathlib import Path
import time

from issuekit.config import IssuekitConfig
from issuekit.gitutil import git_current_branch, git_origin_url, git_status_short, run_git


FETCH_TIMEOUT_SEC = 120.0
_last_successful_fetch: dict[tuple[str, str, str], float] = {}


def enforce_claim_sync(
    cwd: Path | str,
    *,
    config: IssuekitConfig,
    action: str,
    no_sync: bool = False,
) -> None:
    """Require a clean checkout and ff-only sync before an issue is claimed."""

    if no_sync or not config.claim_sync or not config.work_branch:
        return

    checkout = Path(cwd).resolve()
    status = git_status_short(checkout)
    if status is None:
        _raise(
            f"Claim-sync guard blocks {action}: git status failed in checkout "
            f"{checkout}. Inspect the checkout before claiming.",
        )
    if status:
        _raise(
            f"Claim-sync guard blocks {action}: checkout {checkout} has a dirty "
            "working tree. Inspect leftover changes before claiming."
        )

    branch = config.work_branch
    if git_current_branch(checkout) != branch:
        return

    remote = "origin"
    if git_origin_url(checkout) is None:
        return

    now = time.monotonic()
    key = (str(checkout), remote, branch)
    previous = _last_successful_fetch.get(key)
    if previous is not None and now - previous < config.claim_sync_interval_sec:
        return

    fetch = run_git(["fetch", remote, branch], checkout, timeout=FETCH_TIMEOUT_SEC)
    if fetch is None or fetch.returncode != 0:
        _raise(
            _git_failure_message(
                action,
                checkout,
                ["git", "fetch", remote, branch],
                fetch,
            )
        )

    merge_ref = f"{remote}/{branch}"
    merge = run_git(["merge", "--ff-only", merge_ref], checkout, timeout=FETCH_TIMEOUT_SEC)
    if merge is None or merge.returncode != 0:
        _raise(
            _git_failure_message(
                action,
                checkout,
                ["git", "merge", "--ff-only", merge_ref],
                merge,
            )
        )

    _last_successful_fetch[key] = time.monotonic()


def _git_failure_message(
    action: str,
    checkout: Path,
    command: list[str],
    result,
) -> str:
    detail = ""
    if result is not None:
        output = (result.stderr or result.stdout).strip()
        if output:
            detail = f" Output: {output}"
    return (
        f"Claim-sync guard blocks {action}: {' '.join(command)} failed in checkout "
        f"{checkout}.{detail}"
    )


def _raise(message: str) -> None:
    from issuekit.workflow import WorkflowError

    raise WorkflowError(message, code="claim_sync_guard")
