import subprocess
from pathlib import Path

import pytest

from issuekit.gitutil import (
    changed_file_count,
    git_current_branch,
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
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "surrogateescape"
    assert captured["timeout"] == 7
    assert captured["stdin"] == subprocess.DEVNULL


def test_run_git_decodes_utf8_output_independent_of_locale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        stdout = b"\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e.md\0".decode(
            kwargs["encoding"],
            errors=kwargs["errors"],
        )
        return subprocess.CompletedProcess(args[0], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git(["ls-files", "-z"], tmp_path)

    assert result is not None
    assert result.stdout == "日本語.md\0"


def test_run_git_normalizes_non_string_stream_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=b"ok \xe8\xbb\xbd \x87\n",
            stderr=b"err \xff\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git(["status"], tmp_path)

    assert result is not None
    assert result.stdout == "ok \u8efd \ufffd\n"
    assert result.stderr == "err \ufffd\n"


def test_run_git_returns_none_on_subprocess_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert run_git(["status"], tmp_path) is None


def test_run_git_strict_reraises_subprocess_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        run_git(["status"], tmp_path, strict=True)


def test_git_wrappers_normalize_success_and_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    responses = {
        ("--no-pager", "status", "--short"): subprocess.CompletedProcess(
            ["git"], 0, stdout=" M a.py\n?? b.py\n", stderr=""
        ),
        ("rev-parse", "--abbrev-ref", "HEAD"): subprocess.CompletedProcess(
            ["git"], 0, stdout="main\n", stderr=""
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
    assert git_current_branch(tmp_path) == "main"
    assert git_root(tmp_path) == tmp_path.resolve()
    assert git_short_head(tmp_path) == "abc123"
    assert git_origin_url(tmp_path) is None


def _require_git() -> None:
    if run_git(["--version"], ".") is None:
        pytest.skip("git is not available")


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _commit_file(repo: Path) -> None:
    (repo / "file.txt").write_text("hello\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "file.txt")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init")


def test_git_current_branch_returns_branch_name(tmp_path: Path) -> None:
    _require_git()
    _git(tmp_path, "init", "-b", "main")
    _commit_file(tmp_path)

    assert git_current_branch(tmp_path) == "main"


def test_git_current_branch_returns_none_for_detached_head(tmp_path: Path) -> None:
    _require_git()
    _git(tmp_path, "init", "-b", "main")
    _commit_file(tmp_path)
    _git(tmp_path, "checkout", "--detach", "HEAD")

    assert git_current_branch(tmp_path) is None


def test_git_current_branch_returns_none_outside_repo(tmp_path: Path) -> None:
    _require_git()

    assert git_current_branch(tmp_path) is None
