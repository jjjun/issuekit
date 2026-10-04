import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

import issuekit.agentrun.runner as runner_module
from issuekit.agentrun import (
    AgentAdapter,
    AgentPrompt,
    AgentRunConfig,
    AgentRunner,
    ConfigAgentAdapter,
    RunStatus,
    build_adapter,
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


def test_runner_drops_requested_environment_variables(tmp_path: Path, monkeypatch) -> None:
    names = (
        "ISSUEKIT_API_TOKEN",
        "ISSUEKIT_API_USER",
        "ISSUEKIT_API_PASSWORD",
    )
    for name in names:
        monkeypatch.setenv(name, f"secret-{name}")
    monkeypatch.setenv("ISSUEKIT_OTHER_VALUE", "retained")
    script = tmp_path / "script.py"
    script.write_text(
        "import json, os; "
        f"print(json.dumps({{name: os.environ.get(name) for name in {names!r}}}))",
        encoding="utf-8",
        newline="\n",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=10.0,
        drop_env=names,
    )

    assert json.loads(result.stdout_path.read_text(encoding="utf-8")) == dict.fromkeys(
        names
    )
    assert os.environ["ISSUEKIT_OTHER_VALUE"] == "retained"


def test_runner_preserves_environment_variables_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    names = (
        "ISSUEKIT_API_TOKEN",
        "ISSUEKIT_API_USER",
        "ISSUEKIT_API_PASSWORD",
    )
    expected = {name: f"secret-{name}" for name in names}
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    script = tmp_path / "script.py"
    script.write_text(
        "import json, os; "
        f"print(json.dumps({{name: os.environ.get(name) for name in {names!r}}}))",
        encoding="utf-8",
        newline="\n",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=10.0,
    )

    assert json.loads(result.stdout_path.read_text(encoding="utf-8")) == expected


def test_runner_records_codex_jsonl_result_and_preserves_raw_log(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "codex_exec_success.jsonl"
    script = (
        "import pathlib, sys; "
        "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))"
    )
    adapter = build_adapter(
        "codex",
        AgentRunConfig(
            binary=sys.executable,
            adapter="codex",
            headless_argv=("-c", script, str(fixture)),
        ),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = AgentRunner().run(
        adapter,
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=10.0,
    )

    raw_stdout = fixture.read_text(encoding="utf-8")
    final_message = (
        "```review\n"
        '{"verdict":"approve","verification":"uv run pytest","notes":"Looks good."}\n'
        "```"
    )
    assert result.parsed is not None
    assert result.parsed["stdout"] == final_message
    assert result.parsed["session_id"] == "thread-390"
    assert result.parsed["usage_input_tokens"] == "120"
    assert result.parsed["usage_output_tokens"] == "20"
    assert result.stdout_path.read_text(encoding="utf-8") == raw_stdout
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["session_id"] == "thread-390"
    assert status["usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 40,
        "output_tokens": 20,
        "reasoning_output_tokens": 4,
    }
    assert status["final_message"] == final_message
    assert status["is_error"] is False
    restored_status = read_status(result.status_path)
    assert restored_status.session_id == "thread-390"
    assert restored_status.usage["input_tokens"] == 120
    assert restored_status.final_message == final_message


def test_runner_records_claude_envelope_metadata_and_warns_on_disabled_fast_mode(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "claude_result_with_metadata.json"
    adapter = ConfigAgentAdapter(
        "claude",
        AgentRunConfig(
            binary=sys.executable,
            headless_argv=(
                "-c",
                "import pathlib, sys; print(pathlib.Path(sys.argv[1]).read_text())",
                str(fixture),
            ),
            output_format="json",
            speed=True,
            speed_argv=("--settings", '{"fastMode": true}'),
        ),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = AgentRunner().run(
        adapter,
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=10.0,
    )

    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["permission_denials"] == 2
    assert status["permission_denied_tools"] == "Bash, Read"
    assert status["api_error_status"] == "401"
    assert status["fast_mode_state"] == "off"
    assert status["fast_mode_disabled_reason"] == "sdk_opt_in_required"
    assert read_status(result.status_path).permission_denials == 2
    warning = "WARNING: fast mode was requested but is not active: sdk_opt_in_required"
    assert capsys.readouterr().err.count(warning) == 1


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior is required")
@pytest.mark.parametrize(
    "prompt_name",
    [
        "issue-7.md",
        "review-issue-7.md",
        "negotiate-issue-7-round-1-initiator.md",
    ],
)
def test_runner_replaces_prompt_symlinks_without_writing_through_them(
    tmp_path: Path,
    prompt_name: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    run_dir = repo / ".agent-runs"
    run_dir.mkdir()
    prompt_path = run_dir / prompt_name
    target = tmp_path / f"{prompt_name}.target"
    target.write_text("leave this file alone\n", encoding="utf-8")
    prompt_path.symlink_to(target)

    result = AgentRunner().run(
        FakeAdapter([sys.executable, "-c", "pass"]),
        AgentPrompt(prompt_path, "safe prompt", ""),
        repo,
        timeout=10.0,
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "leave this file alone\n"
    assert not prompt_path.is_symlink()
    assert prompt_path.read_text(encoding="utf-8") == "safe prompt"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior is required")
def test_runner_refuses_a_symlinked_run_directory_before_writing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".agent-runs").symlink_to(outside, target_is_directory=True)
    prompt_path = repo / ".agent-runs" / "issue-7.md"

    with pytest.raises(RuntimeError, match=r"Refusing \.agent-runs: it is a symlink"):
        AgentRunner().run(
            FakeAdapter([sys.executable, "-c", "pass"]),
            AgentPrompt(prompt_path, "safe prompt", ""),
            repo,
            timeout=10.0,
        )

    assert list(outside.iterdir()) == []


def test_runner_refuses_tracked_files_under_run_directory_before_writing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    tracked = repo / ".agent-runs" / "tracked.txt"
    tracked.parent.mkdir()
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", ".agent-runs/tracked.txt"], cwd=repo, check=True)
    prompt_path = repo / ".agent-runs" / "issue-7.md"

    with pytest.raises(RuntimeError, match="git tracks 1 files under it"):
        AgentRunner().run(
            FakeAdapter([sys.executable, "-c", "pass"]),
            AgentPrompt(prompt_path, "safe prompt", ""),
            repo,
            timeout=10.0,
        )

    assert not prompt_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior is required")
def test_runner_refuses_a_symlink_at_a_new_stdout_log_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 4, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(runner_module, "datetime", FixedDateTime)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    run_dir = repo / ".agent-runs"
    run_dir.mkdir()
    run_id = "20261004-120000"
    target = tmp_path / "outside.log"
    target.write_text("leave this log alone\n", encoding="utf-8")
    (run_dir / f"{run_id}.out.log").symlink_to(target)

    with pytest.raises(FileExistsError):
        AgentRunner().run(
            FakeAdapter([sys.executable, "-c", "pass"]),
            agent_prompt(tmp_path / "plan.md"),
            repo,
            timeout=10.0,
        )

    assert target.read_text(encoding="utf-8") == "leave this log alone\n"


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


def test_runner_keeps_large_plan_body_out_of_argv(tmp_path: Path) -> None:
    notes = "review note. " * 16_000
    script = tmp_path / "script.py"
    script.write_text(
        "import sys; print(sys.argv[1])", encoding="utf-8", newline="\n"
    )
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    class PromptArgumentAdapter(FakeAdapter):
        def build_argv(
            self,
            prompt: str,
            plan_path: Path,
            session_id: str | None = None,
            resume: bool = False,
        ) -> list[str]:
            return [str(script), prompt]

    result = AgentRunner().run(
        PromptArgumentAdapter([sys.executable]),
        AgentPrompt(
            path=plan,
            body=f"## Review feedback to address\n\n{notes}",
            pointer="Address the review feedback section at the end of the plan file.",
        ),
        repo,
        timeout=10.0,
    )

    assert result.exit_code == 0
    assert plan.read_text(encoding="utf-8") == (
        f"## Review feedback to address\n\n{notes}"
    )
    assert notes not in result.stdout_path.read_text(encoding="utf-8")
    assert "Address the review feedback section" in result.stdout_path.read_text(
        encoding="utf-8"
    )


def test_runner_rejects_overlong_prompt_before_creating_status(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    def reject_launch(*args, **kwargs):
        pytest.fail("overlong prompt must fail before launching")

    monkeypatch.setattr(subprocess, "Popen", reject_launch)

    with pytest.raises(
        ValueError, match="Composed prompt is 24001 characters; limit is 24000"
    ):
        AgentRunner().run(
            FakeAdapter([sys.executable]),
            AgentPrompt(
                path=tmp_path / "plan.md",
                body="plan",
                pointer="x" * 24_001,
            ),
            repo,
            timeout=10.0,
        )

    assert not list((repo / ".agent-runs").glob("*.status.json"))


def test_runner_writes_failed_status_when_process_cannot_launch(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    def fail_launch(*args, **kwargs):
        raise PermissionError(13, "Permission denied", "agent")

    monkeypatch.setattr(subprocess, "Popen", fail_launch)

    with pytest.raises(RuntimeError, match="Could not launch .+ Permission denied"):
        AgentRunner().run(
            FakeAdapter([sys.executable]),
            agent_prompt(tmp_path / "plan.md"),
            repo,
            timeout=10.0,
        )

    status_files = list((repo / ".agent-runs").glob("*.status.json"))
    assert len(status_files) == 1
    status = json.loads(status_files[0].read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["exit_code"] == 1
    assert status["pid"] is None
    assert status["ended_at"] is not None
    assert "Permission denied" in status["failure_reason"]


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


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-only")
def test_runner_timeout_kills_sigterm_ignoring_grandchild(tmp_path: Path) -> None:
    ready_path = tmp_path / "grandchild.pid"
    child_script = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    script = tmp_path / "script.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        f"ready_path = {str(ready_path)!r}\n"
        "subprocess.Popen([sys.executable, '-c', "
        f"{child_script!r}, ready_path])\n"
        "deadline = time.monotonic() + 5\n"
        "while not pathlib.Path(ready_path).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "if not pathlib.Path(ready_path).exists():\n"
        "    raise SystemExit(2)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
        newline="\n",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(tmp_path / "plan.md"),
        repo,
        timeout=0.5,
    )

    grandchild_pid = int(ready_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        process = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(grandchild_pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        process_state = process.stdout.strip()
        if not process_state or process_state.startswith("Z"):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"grandchild process {grandchild_pid} survived process-group kill")

    assert result.timed_out is True


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-only")
def test_runner_keyboard_interrupt_writes_terminal_status_and_kills_group(
    tmp_path: Path, monkeypatch
) -> None:
    grandchild_path = tmp_path / "grandchild.pid"
    child_script = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    script = tmp_path / "script.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        f"grandchild_path = {str(grandchild_path)!r}\n"
        "subprocess.Popen([sys.executable, '-c', "
        f"{child_script!r}, grandchild_path])\n"
        "deadline = time.monotonic() + 5\n"
        "while not pathlib.Path(grandchild_path).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "if not pathlib.Path(grandchild_path).exists():\n"
        "    raise SystemExit(2)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
        newline="\n",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    real_popen = subprocess.Popen
    agent_pids: list[int] = []

    def patched_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        command = args[0]
        if command[0] == sys.executable and str(script) in command:
            agent_pids.append(proc.pid)
            original_wait = proc.wait
            interrupt_next_wait = True

            def interrupt_wait(timeout=None):
                nonlocal interrupt_next_wait
                if interrupt_next_wait:
                    interrupt_next_wait = False
                    deadline = time.monotonic() + 5
                    while not grandchild_path.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert grandchild_path.exists()
                    raise KeyboardInterrupt
                return original_wait(timeout=timeout)

            proc.wait = interrupt_wait
        return proc

    monkeypatch.setattr(subprocess, "Popen", patched_popen)

    with pytest.raises(KeyboardInterrupt):
        AgentRunner().run(
            FakeAdapter([sys.executable, str(script)]),
            agent_prompt(tmp_path / "plan.md"),
            repo,
            timeout=10.0,
        )

    status_paths = list((repo / ".agent-runs").glob("*.status.json"))
    assert len(status_paths) == 1
    status = json.loads(status_paths[0].read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["exit_code"] == 130
    assert status["ended_at"] is not None
    assert len(agent_pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(agent_pids[0], 0)

    grandchild_pid = int(grandchild_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        process = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(grandchild_pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        process_state = process.stdout.strip()
        if not process_state or process_state.startswith("Z"):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"grandchild process {grandchild_pid} survived process-group kill")


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


def test_runner_status_carries_parsed_failure_reason(tmp_path: Path) -> None:
    class FailureReasonAdapter(FakeAdapter):
        def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
            return {
                "failure_reason": "Failed to authenticate: OAuth session expired",
                "terminal_reason": "api_error",
            }

    script = tmp_path / "script.py"
    script.write_text("raise SystemExit(1)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = FailureReasonAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["failure_reason"] == "Failed to authenticate: OAuth session expired"
    assert status["terminal_reason"] == "api_error"


def test_runner_writes_terminal_status_when_parse_output_raises(tmp_path: Path) -> None:
    class RaisingAdapter(FakeAdapter):
        def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
            raise ValueError("boom")

    script = tmp_path / "script.py"
    script.write_text("raise SystemExit(1)")
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = RaisingAdapter([sys.executable, str(script)])
    result = AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    assert result.parsed is None
    assert result.status_path is not None
    status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["exit_code"] == 1
    assert status["failure_reason"] is None
    assert status["terminal_reason"] is None


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


def test_runner_status_uses_latest_codex_stdout_event(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "import json, sys, time\n"
        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message'}}), flush=True)\n"
        "print('Reading additional input from stdin...', file=sys.stderr, flush=True)\n"
        "time.sleep(1.2)\n"
        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'echo ' + 'x' * 300}}), flush=True)\n"
        "time.sleep(1.2)\n",
        encoding="utf-8",
        newline="\n",
    )
    plan = tmp_path / "plan.md"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = AgentRunner().run(
        FakeAdapter([sys.executable, str(script)]),
        agent_prompt(plan),
        repo,
        timeout=10.0,
    )

    assert result.status_path is not None
    final_status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert final_status["last_log_line"].startswith(
        "item.completed command_execution: echo "
    )
    assert len(final_status["last_log_line"]) <= 200
    assert final_status["last_log_line"] != "Reading additional input from stdin..."


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
