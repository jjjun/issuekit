import os
import subprocess
from pathlib import Path

import pytest

from issuekit.agentrun.git import changed_file_count, git_status_short
from issuekit.agentrun.run_dir import prepare_run_dir


def test_git_status_short_disables_optional_locks(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0]
        return subprocess.CompletedProcess(args[0], 0, stdout=" M file.py\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert git_status_short(tmp_path) == "M file.py"
    assert captured["args"] == [
        "git",
        "-c",
        "core.fsmonitor=false",
        "--no-optional-locks",
        "-c",
        "core.quotepath=false",
        "--no-pager",
        "status",
        "--short",
    ]


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX fsmonitor script")
def test_agentrun_status_and_changed_count_disable_repo_fsmonitor(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    (tmp_path / "tracked.txt").write_text("content\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
    )

    marker = tmp_path.parent / f"{tmp_path.name}-agentrun-fsmonitor-marker"
    script = tmp_path.parent / f"{tmp_path.name}-agentrun-fsmonitor.sh"
    script.write_text(
        f"#!/bin/sh\nprintf invoked >> '{marker}'\nprintf 'token\\n\\n'\n",
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.fsmonitor", str(script)],
        cwd=tmp_path,
        check=True,
    )

    subprocess.run(
        ["git", "status", "--short"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert marker.exists()
    marker.unlink()

    assert git_status_short(tmp_path) == ""
    assert changed_file_count(tmp_path) == 0
    assert not marker.exists()


def test_prepare_run_dir_uses_fsmonitor_and_hooks_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        hooks_path = Path(argv[4].split("=", 1)[1])
        assert hooks_path.is_dir()
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    prepared = prepare_run_dir(tmp_path)

    assert prepared == (tmp_path / ".agent-runs").absolute()
    assert captured["argv"][:4] == [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
    ]
    assert captured["argv"][5:] == ["ls-files", "-z", "--", ".agent-runs"]
