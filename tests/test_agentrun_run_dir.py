import subprocess
from pathlib import Path

from issuekit.agentrun.run_dir import prepare_run_dir


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
