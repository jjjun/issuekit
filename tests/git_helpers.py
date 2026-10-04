import subprocess
from pathlib import Path


def init_git_repo(
    path: Path,
    *,
    message: str = "initial",
    autocrlf: bool = False,
    allow_empty: bool = False,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True
    )
    if autocrlf:
        subprocess.run(
            ["git", "config", "core.autocrlf", "false"], cwd=path, check=True
        )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    commit = ["git", "commit", "-q"]
    if allow_empty:
        commit.append("--allow-empty")
    subprocess.run([*commit, "-m", message], cwd=path, check=True)
