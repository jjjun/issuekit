import subprocess
from pathlib import Path

from issuekit.agentrun.git import git_status_short


def test_git_status_short_disables_optional_locks(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0]
        return subprocess.CompletedProcess(args[0], 0, stdout=" M file.py\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert git_status_short(tmp_path) == "M file.py"
    assert captured["args"] == [
        "git",
        "--no-optional-locks",
        "-c",
        "core.quotepath=false",
        "--no-pager",
        "status",
        "--short",
    ]
