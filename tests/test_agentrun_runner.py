import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from issuekit.agentrun import (
    AgentAdapter,
    AgentPrompt,
    AgentRunConfig,
    AgentRunner,
    ConfigAgentAdapter,
    RunStatus,
)
from issuekit.agentrun.runner import _RunWatcher
from issuekit.agentrun.status import read_status, status_path, write_status


class FakeAdapter(AgentAdapter):
    """Fake adapter for testing the runner without a real agent."""

    def __init__(self, command: list[str]) -> None:
        self.command = command

    def resolve_binary(self) -> Path:
        return Path(self.command[0])

    def build_argv(
        self,
        prompt: str,
        plan_path: Path,
        session_id: str | None = None,
        resume: bool = False,
    ) -> list[str]:
        return self.command[1:]

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        return {}


def agent_prompt(path: Path) -> AgentPrompt:
    return AgentPrompt(path=path, body="plan", pointer="")


def running_status() -> RunStatus:
    return RunStatus(
        run_id="run-a",
        agent="codex",
        issue=307,
        status="running",
        pid=123,
        started_at="2026-07-26T12:00:00",
        ended_at=None,
        elapsed_sec=None,
        exit_code=None,
        plan=".agent-runs/issue-307.md",
        stdout_log=".agent-runs/run-a.out.log",
        agent_log=".agent-runs/run-a.agent.log",
    )


def test_runner_captures_stdout_stderr_and_returns_result(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import sys; print('hello out'); print('hello err', file=sys.stderr)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    runner = AgentRunner()
    result = runner.run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout_path.exists()
    assert result.agent_log_path.exists()
    assert "hello out" in result.stdout_path.read_text(encoding="utf-8")
    assert "hello err" in result.agent_log_path.read_text(encoding="utf-8")
    assert result.elapsed_sec >= 0
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["exit_code"] == 0
    assert status["elapsed_sec"] >= 0
    assert status["stdout_log"].endswith(".out.log")
    assert status["agent_log"].endswith(".agent.log")


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not used on Windows")
def test_runner_creates_owner_only_artifacts(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    reservation_modes: list[int] = []
    runner = AgentRunner()
    release_reservation = runner._release_run_id_reservation

    def capture_reservation_mode(path: Path) -> None:
        reservation_modes.append(path.stat().st_mode & 0o777)
        release_reservation(path)

    monkeypatch.setattr(runner, "_release_run_id_reservation", capture_reservation_mode)
    result = runner.run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=10.0,
    )

    assert (repo / ".agent-runs").stat().st_mode & 0o777 == 0o700
    assert result.stdout_path.stat().st_mode & 0o777 == 0o600
    assert result.agent_log_path.stat().st_mode & 0o777 == 0o600
    assert result.status_path is not None
    assert result.status_path.stat().st_mode & 0o777 == 0o600
    assert reservation_modes == [0o600]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not used on Windows")
def test_runner_tightens_an_existing_run_directory(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    run_dir = repo / ".agent-runs"
    run_dir.mkdir(mode=0o755)
    run_dir.chmod(0o755)

    AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=10.0,
    )

    assert run_dir.stat().st_mode & 0o777 == 0o700


def test_runner_uses_explicit_run_directory(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runtime-files"

    result = AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(plan),
        repo,
        timeout=10.0,
        run_dir=run_dir,
    )

    assert result.stdout_path.parent == run_dir
    assert result.agent_log_path.parent == run_dir


def test_runner_uses_caller_prompt(
    tmp_path: Path,
) -> None:
    class PromptCaptureAdapter(FakeAdapter):
        prompt: str = ""

        def build_argv(
            self,
            prompt: str,
            plan_path: Path,
            session_id: str | None = None,
            resume: bool = False,
        ) -> list[str]:
            self.prompt = prompt
            return super().build_argv(
                prompt, plan_path, session_id=session_id, resume=resume
            )

    script = tmp_path / "script.py"
    script.write_text("pass")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = PromptCaptureAdapter([sys.executable, str(script)])
    AgentRunner().run(
        adapter,
        AgentPrompt(path=plan, body="plan", pointer="Caller-owned prompt."),
        repo,
        timeout=10.0,
    )

    assert adapter.prompt == "Caller-owned prompt."


def test_runner_passes_session_id_through_to_argv(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "import json, sys; print(json.dumps(sys.argv[1:]))",
        encoding="utf-8",
        newline="\n",
    )
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    run_config = AgentRunConfig(
        binary=sys.executable,
        headless_argv=(str(script),),
        resumable=True,
        session_flag="--session-id",
    )

    adapter = ConfigAgentAdapter("python-agent", run_config)
    result = AgentRunner().run(
        adapter,
        agent_prompt(plan),
        repo,
        timeout=10.0,
        session_id="123e4567-e89b-12d3-a456-426614174000",
    )

    argv = json.loads(result.stdout_path.read_text(encoding="utf-8"))
    assert argv[-2:] == ["--session-id", "123e4567-e89b-12d3-a456-426614174000"]


def test_runner_replaces_invalid_log_bytes_before_parsing(tmp_path: Path) -> None:
    class ParsingAdapter(FakeAdapter):
        def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
            return {"stdout": stdout, "stderr": stderr}

    script = tmp_path / "script.py"
    script.write_text(
        "import os; os.write(1, b'valid\\xfftext\\n'); os.write(2, b'err\\xfe\\n')"
    )
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = ParsingAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

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
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script), str(repo)])
    result = AgentRunner().run(
        adapter,
        agent_prompt(plan),
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
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    runner = AgentRunner()
    result = runner.run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "read: ''" in result.stdout_path.read_text(encoding="utf-8")


def test_runner_kills_on_timeout(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(60)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    runner = AgentRunner()
    start = time.monotonic()
    result = runner.run(adapter, agent_prompt(plan), repo, timeout=0.5)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert elapsed < 5.0
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "timed_out"
    assert status["exit_code"] != 0


def test_runner_kills_when_abort_event_is_set(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(60)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    abort_event = threading.Event()
    abort_event.set()

    result = AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(plan),
        repo,
        timeout=10.0,
        abort_event=abort_event,
    )

    assert result.timed_out is True
    assert result.exit_code != 0
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "timed_out"


def test_runner_status_is_failed_for_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("raise SystemExit(7)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.exit_code == 7
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["exit_code"] == 7


def test_runner_git_status_short(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import pathlib, sys; (pathlib.Path(sys.argv[1]) / 'new.txt').write_text('x')")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    adapter = FakeAdapter([sys.executable, str(script), str(repo)])
    runner = AgentRunner()
    result = runner.run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.timed_out is False
    assert result.status_short is not None
    assert "new.txt" in result.status_short


def test_runner_writes_prompt_file_when_it_does_not_exist(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = tmp_path / "nosuch.md"
    adapter = FakeAdapter([sys.executable, "-c", "pass"])
    runner = AgentRunner()
    runner.run(adapter, agent_prompt(plan), repo)

    assert plan.read_text(encoding="utf-8") == "plan"


def test_runner_missing_repo_raises(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    adapter = FakeAdapter([sys.executable, "-c", "pass"])
    runner = AgentRunner()
    with pytest.raises(FileNotFoundError, match="Repo directory not found"):
        runner.run(adapter, agent_prompt(plan), tmp_path / "nosuch")


def test_runner_status_gains_last_log_fields_during_run(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "import sys, time; print('log-one', file=sys.stderr); time.sleep(0.8); print('log-two', file=sys.stderr); time.sleep(0.8)"
    )
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.status_path is not None
    final_status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert final_status["last_log_line"] == "log-two"
    assert final_status["last_log_at"] is not None
    assert final_status["heartbeat_at"] is not None


def test_runner_writer_survives_a_failing_tick(tmp_path: Path, monkeypatch) -> None:
    real_tick = _RunWatcher._tick
    state = {"failed_once": False}

    def flaky_tick(self):
        if not state["failed_once"]:
            state["failed_once"] = True
            raise PermissionError("WinError 5: Access is denied")
        return real_tick(self)

    monkeypatch.setattr(_RunWatcher, "_tick", flaky_tick)

    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(1.5)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    # The first tick raised, but the loop kept going and the run completed normally.
    assert state["failed_once"] is True
    assert result.exit_code == 0
    assert result.status_path is not None
    final_status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert final_status["status"] == "completed"
    assert final_status["heartbeat_at"] is not None


def test_watcher_slow_tick_cannot_overwrite_terminal_status(
    tmp_path: Path, monkeypatch
) -> None:
    run_status = running_status()
    run_status_path = status_path(tmp_path, run_status.run_id)
    agent_log_path = tmp_path / "agent.log"
    agent_log_path.write_text("working\n", encoding="utf-8", newline="\n")
    write_status(run_status_path, run_status)
    changed_file_count_started = threading.Event()
    release_changed_file_count = threading.Event()

    def slow_changed_file_count(_repo: Path) -> int:
        changed_file_count_started.set()
        assert release_changed_file_count.wait(timeout=5)
        return 0

    monkeypatch.setattr(
        "issuekit.agentrun.runner.changed_file_count", slow_changed_file_count
    )
    watcher = _RunWatcher(
        run_status_path=run_status_path,
        run_status=run_status,
        repo=tmp_path,
        agent_log_path=agent_log_path,
        enable_heartbeat=True,
        start_time=time.monotonic(),
    )

    watcher.start()
    assert changed_file_count_started.wait(timeout=5)
    write_status(
        run_status_path,
        replace(
            read_status(run_status_path),
            status="completed",
            ended_at="2026-07-26T12:00:01",
            elapsed_sec=1.0,
            exit_code=0,
        ),
    )
    release_changed_file_count.set()
    watcher.stop()

    assert read_status(run_status_path).status == "completed"


def test_watcher_skips_changed_file_count_without_heartbeat(
    tmp_path: Path, monkeypatch
) -> None:
    run_status = running_status()
    run_status_path = status_path(tmp_path, run_status.run_id)
    agent_log_path = tmp_path / "agent.log"
    write_status(run_status_path, run_status)

    def unexpected_changed_file_count(_repo: Path) -> int:
        raise AssertionError("changed_file_count should not be called")

    monkeypatch.setattr(
        "issuekit.agentrun.runner.changed_file_count",
        unexpected_changed_file_count,
    )
    watcher = _RunWatcher(
        run_status_path=run_status_path,
        run_status=run_status,
        repo=tmp_path,
        agent_log_path=agent_log_path,
        enable_heartbeat=False,
        start_time=time.monotonic(),
    )

    watcher._tick()

    assert read_status(run_status_path).heartbeat_at is not None


def test_runner_prints_agent_runs_note_when_dir_is_created(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    captured = capsys.readouterr()
    assert ".agent-runs/ is gitignored run-log storage" in captured.err


def test_runner_does_not_print_agent_runs_note_when_dir_already_exists(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".agent-runs").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    captured = capsys.readouterr()
    assert ".agent-runs/ is gitignored run-log storage" not in captured.err


def test_runner_passes_run_specific_implementer_report_path(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['ISSUEKIT_IMPLEMENTER_REPORT_FILE']).write_text('Verified.')\n"
    )
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    result = AgentRunner().run(
        adapter,
        agent_prompt(plan),
        repo,
        timeout=10.0,
        implementer_report=True,
    )

    assert result.report_path is not None
    assert result.report_path.name.endswith(".report.md")
    assert result.report_path.read_text(encoding="utf-8") == "Verified."


def test_runner_heartbeat_suppressed_when_stderr_not_tty(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(0.3)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    adapter = FakeAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    captured = capsys.readouterr()
    assert "running run=" not in captured.err


def test_runner_heartbeat_emitted_when_follow_is_set(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(0.3)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    adapter = FakeAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0, follow=True)

    captured = capsys.readouterr()
    assert "running run=" in captured.err
