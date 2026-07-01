import subprocess
from pathlib import Path

from issuekit.gitutil import (
    changed_file_count,
    git_origin_url,
    git_root,
    git_short_head,
    git_status_short,
    run_git,
)


def test_run_git_redirects_stdin_and_normalizes_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0]
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="out\n", stderr="err\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git(["status", "--short"], tmp_path, timeout=7)

    assert result is not None
    assert result.returncode == 0
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert captured["args"] == ["git", "status", "--short"]
    assert captured["cwd"] == str(tmp_path)
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 7
    assert captured["stdin"] == subprocess.DEVNULL


def test_run_git_returns_none_on_subprocess_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert run_git(["status"], tmp_path) is None


def test_git_wrappers_normalize_success_and_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    responses = {
        ("--no-pager", "status", "--short"): subprocess.CompletedProcess(
            ["git"], 0, stdout=" M a.py\n?? b.py\n", stderr=""
        ),
        ("rev-parse", "--short", "HEAD"): subprocess.CompletedProcess(
            ["git"], 0, stdout="abc123\n", stderr=""
        ),
        ("rev-parse", "--show-toplevel"): subprocess.CompletedProcess(
            ["git"], 0, stdout=f"{tmp_path}\n", stderr=""
        ),
        ("config", "--get", "remote.origin.url"): subprocess.CompletedProcess(
            ["git"], 1, stdout="", stderr=""
        ),
    }

    def fake_run(argv, **kwargs):
        return responses[tuple(argv[1:])]

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert git_status_short(tmp_path) == "M a.py\n?? b.py"
    assert git_status_short(tmp_path, strip=False) == " M a.py\n?? b.py\n"
    assert changed_file_count(tmp_path) == 2
    assert git_root(tmp_path) == tmp_path.resolve()
    assert git_short_head(tmp_path) == "abc123"
    assert git_origin_url(tmp_path) is None
