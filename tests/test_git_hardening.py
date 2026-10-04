import io
import os
import subprocess
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.agents import review as review_agent
from issuekit.agents import run_claimed
from issuekit.commands import check_encoding
from issuekit.encoding import changed_line_numbers
from issuekit.gitutil import GitStatusEntry


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _init_textconv_repo(repo: Path) -> tuple[Path, Path]:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".gitattributes").write_text("*.txt diff=x\n", encoding="utf-8", newline="\n")
    (repo / "source.txt").write_text("before\n", encoding="utf-8", newline="\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    script = repo.parent / f"{repo.name}-textconv.sh"
    marker = repo.parent / f"{repo.name}-textconv-marker"
    script.write_text(
        f"#!/bin/sh\nprintf invoked >> '{marker}'\ncat \"$1\"\n",
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    _git(repo, "config", "diff.x.textconv", str(script))
    (repo / "source.txt").write_text("after\n", encoding="utf-8", newline="\n")
    return marker, script


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX textconv script")
@pytest.mark.parametrize(
    "operation",
    ["review", "numstat", "encoding_scan", "check_encoding_changed"],
)
def test_issuekit_diff_operations_disable_textconv(
    tmp_path: Path,
    monkeypatch,
    operation: str,
) -> None:
    marker, _script = _init_textconv_repo(tmp_path)

    _git(tmp_path, "diff", "--", "source.txt")
    assert marker.exists()
    marker.unlink()

    if operation == "review":
        review_agent._collect_git_diff_context(tmp_path)
    elif operation == "numstat":
        snapshot = run_claimed.ImplementationChangeSnapshot(
            root=tmp_path.resolve(),
            status_entries=(GitStatusEntry(" M", Path("source.txt")),),
            changed_paths=(Path("source.txt"),),
            readable_paths=(Path("source.txt"),),
        )
        run_claimed._warn_heavy_deletions(
            snapshot,
            tmp_path,
            deletion_threshold=0,
            err=io.StringIO(),
        )
    elif operation == "encoding_scan":
        changed_line_numbers(tmp_path, (Path("source.txt"),))
    else:
        monkeypatch.chdir(tmp_path)
        assert cli.main(["check-encoding", "--changed"]) == 0

    assert not marker.exists()


def test_check_encoding_diff_commands_disable_external_drivers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _init_textconv_repo(tmp_path)
    captured: list[list[str]] = []
    original_run_git = check_encoding.run_git

    def record_run_git(args, cwd, **kwargs):
        if "diff" in args:
            captured.append(list(args))
        return original_run_git(args, cwd, **kwargs)

    monkeypatch.setattr(check_encoding, "run_git", record_run_git)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--changed"]) == 0

    assert len(captured) == 2
    assert all("--no-ext-diff" in args for args in captured)
    assert all("--no-textconv" in args for args in captured)
