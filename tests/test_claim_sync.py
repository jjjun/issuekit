import os
import subprocess
from pathlib import Path

import pytest

from issuekit.config import IssuekitConfig
from issuekit.errors import WorkflowError
from issuekit.gitutil import GitResult
from issuekit.guards.claim_sync import enforce_claim_sync


def test_claim_sync_noops_without_work_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_status(cwd):
        raise AssertionError("status should not run")

    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", fail_status)

    enforce_claim_sync(tmp_path, config=IssuekitConfig(), action="claim-next")


def test_claim_sync_blocks_dirty_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: " M file.py")

    with pytest.raises(WorkflowError) as excinfo:
        enforce_claim_sync(
            tmp_path,
            config=IssuekitConfig(work_branch="main"),
            action="claim issue #1",
        )

    message = str(excinfo.value)
    assert "Claim-sync guard blocks claim issue #1" in message
    assert str(tmp_path.resolve()) in message
    assert "dirty working tree" in message
    assert "Commit or stash inspected changes before claiming" in message
    assert "--no-sync" in message
    assert excinfo.value.code == "claim_sync_guard"


def test_claim_sync_skips_fetch_when_origin_is_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_origin_url", lambda cwd: None)

    def fail_run_git(args, cwd, *, timeout=30):
        raise AssertionError("fetch should not run without origin")

    monkeypatch.setattr("issuekit.guards.claim_sync.run_git", fail_run_git)

    enforce_claim_sync(
        tmp_path,
        config=IssuekitConfig(work_branch="main"),
        action="claim-next",
    )


def test_claim_sync_fetches_and_fast_forwards_configured_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_origin_url", lambda cwd: "https://example/repo.git")

    def fake_run_git(args, cwd, *, timeout=30):
        calls.append((list(args), cwd, timeout))
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("issuekit.guards.claim_sync.run_git", fake_run_git)

    enforce_claim_sync(
        tmp_path,
        config=IssuekitConfig(work_branch="main"),
        action="claim-next",
    )

    checkout = tmp_path.resolve()
    assert len(calls) == 2
    hooks_option = calls[0][0][:2]
    assert hooks_option[0] == "-c"
    assert hooks_option[1].startswith("core.hooksPath=")
    assert calls == [
        (
            [*hooks_option, "fetch", "--end-of-options", "origin", "main"],
            checkout,
            120.0,
        ),
        (
            [*hooks_option, "merge", "--ff-only", "--end-of-options", "origin/main"],
            checkout,
            120.0,
        ),
    ]


def test_claim_sync_throttles_successful_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_origin_url", lambda cwd: "https://example/repo.git")

    def fake_run_git(args, cwd, *, timeout=30):
        calls.append(list(args))
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("issuekit.guards.claim_sync.run_git", fake_run_git)

    config = IssuekitConfig(work_branch="main", claim_sync_interval_sec=60.0)
    enforce_claim_sync(tmp_path, config=config, action="claim-next")
    enforce_claim_sync(tmp_path, config=config, action="claim-next")

    assert len(calls) == 2
    hooks_option = calls[0][:2]
    assert calls[0] == [*hooks_option, "fetch", "--end-of-options", "origin", "main"]
    assert calls[1] == [
        *hooks_option,
        "merge",
        "--ff-only",
        "--end-of-options",
        "origin/main",
    ]


def test_claim_sync_fetch_failure_blocks_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_current_branch", lambda cwd: "main")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_origin_url", lambda cwd: "https://example/repo.git")
    monkeypatch.setattr(
        "issuekit.guards.claim_sync.run_git",
        lambda args, cwd, *, timeout=30: GitResult(
            returncode=1,
            stdout="",
            stderr="network unavailable",
        ),
    )

    with pytest.raises(WorkflowError) as excinfo:
        enforce_claim_sync(
            tmp_path,
            config=IssuekitConfig(work_branch="main"),
            action="claim-next",
        )

    message = str(excinfo.value)
    assert "git fetch origin main failed" in message
    assert str(tmp_path.resolve()) in message
    assert "network unavailable" in message
    assert "Resolve the Git failure and retry" in message
    assert "--no-sync" in message


def test_claim_sync_status_failure_names_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: None)

    with pytest.raises(WorkflowError) as excinfo:
        enforce_claim_sync(
            tmp_path,
            config=IssuekitConfig(work_branch="main"),
            action="claim-next",
        )

    message = str(excinfo.value)
    assert "Repair the checkout and retry" in message
    assert "--no-sync" in message
    assert excinfo.value.code == "claim_sync_guard"


def test_claim_sync_skips_fetch_when_current_branch_is_not_work_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", lambda cwd: "")
    monkeypatch.setattr("issuekit.guards.claim_sync.git_current_branch", lambda cwd: "feature")

    def fail_origin(cwd):
        raise AssertionError("origin lookup should not run for another branch")

    def fail_run_git(args, cwd, *, timeout=30):
        raise AssertionError("fetch should not run for another branch")

    monkeypatch.setattr("issuekit.guards.claim_sync.git_origin_url", fail_origin)
    monkeypatch.setattr("issuekit.guards.claim_sync.run_git", fail_run_git)

    enforce_claim_sync(
        tmp_path,
        config=IssuekitConfig(work_branch="main"),
        action="claim issue #1",
    )


def test_claim_sync_allows_explicit_no_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_status(cwd):
        raise AssertionError("status should not run")

    monkeypatch.setattr("issuekit.guards.claim_sync.git_status_short", fail_status)

    enforce_claim_sync(
        tmp_path,
        config=IssuekitConfig(work_branch="main"),
        action="claim-next",
        no_sync=True,
    )


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX post-merge hook")
def test_claim_sync_fast_forward_does_not_run_post_merge_hook(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    hooks = tmp_path / "hooks"
    marker = tmp_path / "post-merge-marker"
    hooks.mkdir()

    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    seed.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=seed, check=True)
    (seed / "tracked.txt").write_text("before\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=seed, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=seed,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True)
    subprocess.run(
        ["git", "clone", str(origin), str(checkout)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    post_merge = hooks / "post-merge"
    post_merge.write_text(
        f"#!/bin/sh\nprintf invoked >> '{marker}'\n",
        encoding="utf-8",
        newline="\n",
    )
    post_merge.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hooks)],
        cwd=checkout,
        check=True,
    )

    (seed / "tracked.txt").write_text("after\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=seed, check=True)
    subprocess.run(
        ["git", "commit", "-m", "advance"],
        cwd=seed,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True)

    enforce_claim_sync(
        checkout,
        config=IssuekitConfig(work_branch="main", claim_sync_interval_sec=0),
        action="claim-next",
    )

    assert (checkout / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert not marker.exists()
