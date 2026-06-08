import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from issuekit.agents.adapters.kimi import KimiAdapter
from issuekit.agents.runner import AgentAdapter, AgentResult, AgentRunner


class FakeAdapter(AgentAdapter):
    """Fake adapter for testing the runner without a real agent."""

    def __init__(self, command: list[str]) -> None:
        self.command = command

    def resolve_binary(self) -> Path:
        return Path(self.command[0])

    def build_argv(self, prompt: str, plan_path: Path) -> list[str]:
        return self.command[1:]

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        return {}


def test_runner_captures_stdout_stderr_and_returns_result(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import sys; print('hello out'); print('hello err', file=sys.stderr)")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    runner = AgentRunner()
    result = runner.run(adapter, plan, repo, timeout=10.0)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout_path.exists()
    assert result.stderr_path.exists()
    assert "hello out" in result.stdout_path.read_text(encoding="utf-8")
    assert "hello err" in result.stderr_path.read_text(encoding="utf-8")
    assert result.elapsed_sec >= 0
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["exit_code"] == 0
    assert status["elapsed_sec"] >= 0
    assert status["stdout_log"].endswith(".out.log")
    assert status["stderr_log"].endswith(".err.log")


def test_runner_replaces_invalid_log_bytes_before_parsing(tmp_path: Path) -> None:
    class ParsingAdapter(FakeAdapter):
        def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
            return {"stdout": stdout, "stderr": stderr}

    script = tmp_path / "script.py"
    script.write_text(
        "import os; os.write(1, b'valid\\xfftext\\n'); os.write(2, b'err\\xfe\\n')"
    )
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = ParsingAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, plan, repo, timeout=10.0)

    assert result.parsed is not None
    assert "valid\ufffdtext" in result.parsed["stdout"]
    assert "err\ufffd" in result.parsed["stderr"]


def test_runner_status_is_running_while_process_is_active(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "\n".join(
            [
                "import json, pathlib, sys, time",
                "repo = pathlib.Path(sys.argv[1])",
                "deadline = time.time() + 5",
                "while time.time() < deadline:",
                "    files = list((repo / '.agent-runs').glob('*.status.json'))",
                "    if files:",
                "        data = json.loads(files[0].read_text(encoding='utf-8'))",
                "        (repo / 'seen-status.json').write_text(json.dumps(data), encoding='utf-8')",
                "        break",
                "    time.sleep(0.01)",
                "else:",
                "    raise SystemExit(2)",
            ]
        )
    )
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script), str(repo)])
    result = AgentRunner().run(
        adapter,
        plan,
        repo,
        timeout=10.0,
        agent_name="codex",
        issue_id=41,
    )

    seen_status = json.loads((repo / "seen-status.json").read_text(encoding="utf-8"))
    assert seen_status["status"] == "running"
    assert seen_status["agent"] == "codex"
    assert seen_status["issue"] == 41
    assert seen_status["plan"] == plan.resolve().as_posix()

    final_status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert final_status["status"] == "completed"
    assert final_status["ended_at"] is not None


def test_runner_uses_devnull_stdin_and_does_not_hang(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import sys; data = sys.stdin.read(); print('read:', repr(data))")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    runner = AgentRunner()
    result = runner.run(adapter, plan, repo, timeout=10.0)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "read: ''" in result.stdout_path.read_text(encoding="utf-8")


def test_runner_kills_on_timeout(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(60)")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    runner = AgentRunner()
    start = time.monotonic()
    result = runner.run(adapter, plan, repo, timeout=0.5)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert elapsed < 5.0
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "timed_out"
    assert status["exit_code"] != 0


def test_runner_status_is_failed_for_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("raise SystemExit(7)")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, plan, repo, timeout=10.0)

    assert result.exit_code == 7
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["exit_code"] == 7


def test_runner_git_status_short(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import pathlib, sys; (pathlib.Path(sys.argv[1]) / 'new.txt').write_text('x')")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    adapter = FakeAdapter([sys.executable, str(script), str(repo)])
    runner = AgentRunner()
    result = runner.run(adapter, plan, repo, timeout=10.0)

    assert result.timed_out is False
    assert result.status_short is not None
    assert "new.txt" in result.status_short


def test_runner_missing_plan_raises(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = FakeAdapter([sys.executable, "-c", "pass"])
    runner = AgentRunner()
    with pytest.raises(FileNotFoundError, match="Plan file not found"):
        runner.run(adapter, tmp_path / "nosuch.md", repo)


def test_runner_missing_repo_raises(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    adapter = FakeAdapter([sys.executable, "-c", "pass"])
    runner = AgentRunner()
    with pytest.raises(FileNotFoundError, match="Repo directory not found"):
        runner.run(adapter, plan, tmp_path / "nosuch")


def test_kimi_adapter_argv_contains_p_and_never_auto() -> None:
    adapter = KimiAdapter()
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "-p" in argv
    assert "--auto" not in argv
    assert "-y" not in argv
    assert "--output-format" in argv


def test_kimi_adapter_argv_includes_model() -> None:
    adapter = KimiAdapter(model="k2")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "k2"


def test_kimi_adapter_parse_output_extracts_resume_id_from_stderr() -> None:
    adapter = KimiAdapter()
    stdout = "Answer\n"
    stderr = "thinking...\nTo resume this session: kimi -r abc123\n"
    parsed = adapter.parse_output(stdout, stderr)
    assert parsed["resume_session_id"] == "abc123"
    assert parsed["stdout"] == stdout
    assert parsed["stderr"] == stderr


def test_kimi_adapter_resolve_binary_raises_when_not_found(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("issuekit.agents.runner.shutil.which", lambda _cmd: None)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(p).replace("~", str(tmp_path)))
    adapter = KimiAdapter()
    with pytest.raises(RuntimeError, match="not found"):
        adapter.resolve_binary()
