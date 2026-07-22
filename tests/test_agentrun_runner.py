import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from issuekit.agentrun.adapters.kimi import KimiAdapter
from issuekit.agents.registry import resolve_adapter
from issuekit.agentrun import AgentAdapter, AgentPrompt, AgentResult, AgentRunner, ConfigAgentAdapter
from issuekit.config import AgentRunConfig, IssuekitConfig


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
    ) -> list[str]:
        return self.command[1:]

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        return {}


def agent_prompt(path: Path) -> AgentPrompt:
    return AgentPrompt(path=path, body="plan", pointer="")


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


def test_runner_uses_explicit_run_directory(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
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
        ) -> list[str]:
            self.prompt = prompt
            return super().build_argv(prompt, plan_path, session_id=session_id)

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
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    config = IssuekitConfig(
        agents=(
            (
                "python-agent",
                AgentRunConfig(
                    binary=sys.executable,
                    headless_argv=(str(script),),
                    resumable=True,
                    session_flag="--session-id",
                ),
            ),
        )
    )

    adapter = ConfigAgentAdapter("python-agent", dict(config.agents)["python-agent"])
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
    plan.write_text("plan")
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
    plan.write_text("plan")
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
    plan.write_text("plan")
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
    plan.write_text("plan")
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


def test_runner_status_is_failed_for_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("raise SystemExit(7)")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
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
    plan.write_text("plan")
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
    plan.write_text("plan")
    adapter = FakeAdapter([sys.executable, "-c", "pass"])
    runner = AgentRunner()
    with pytest.raises(FileNotFoundError, match="Repo directory not found"):
        runner.run(adapter, agent_prompt(plan), tmp_path / "nosuch")


def test_kimi_adapter_argv_contains_p_and_never_auto() -> None:
    adapter = resolve_adapter("kimi")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "-p" in argv
    assert "--auto" not in argv
    assert "-y" not in argv
    assert "--output-format" in argv


def test_kimi_adapter_argv_includes_model() -> None:
    adapter = resolve_adapter("kimi", model="k2")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "k2"


def test_claude_adapter_argv_build_full_shape() -> None:
    adapter = resolve_adapter("claude")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert argv[0] == "-p"
    assert argv[1].startswith("prompt")
    assert argv[2:] == [
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "text",
    ]


def test_claude_adapter_argv_appends_model_when_supplied() -> None:
    adapter = resolve_adapter("claude", model="claude-opus-4-8")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert argv[:6] == [
        "-p",
        argv[1],
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "text",
    ]
    assert argv[6:] == ["--model", "claude-opus-4-8"]


def test_config_adapter_appends_session_flag_only_when_resumable() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "resumable",
                AgentRunConfig(
                    binary="agent",
                    headless_argv=("run",),
                    resumable=True,
                    session_flag="--session-id",
                ),
            ),
            (
                "plain",
                AgentRunConfig(
                    binary="agent",
                    headless_argv=("run",),
                    session_flag="--session-id",
                ),
            ),
        )
    )

    resumable = ConfigAgentAdapter("resumable", dict(config.agents)["resumable"])
    plain = ConfigAgentAdapter("plain", dict(config.agents)["plain"])

    assert resumable.supports_session_resume() is True
    assert plain.supports_session_resume() is False
    assert resumable.build_argv(
        "prompt",
        Path("/plan.md"),
        session_id="123e4567-e89b-12d3-a456-426614174000",
    )[-2:] == ["--session-id", "123e4567-e89b-12d3-a456-426614174000"]
    assert resumable.build_argv("prompt", Path("/plan.md")) == ["run", "prompt"]
    assert plain.build_argv(
        "prompt",
        Path("/plan.md"),
        session_id="123e4567-e89b-12d3-a456-426614174000",
    ) == ["run", "prompt"]


def test_claude_adapter_argv_appends_session_id_when_supplied() -> None:
    adapter = resolve_adapter("claude")
    argv = adapter.build_argv(
        "prompt",
        Path("/plan.md"),
        session_id="123e4567-e89b-12d3-a456-426614174000",
    )

    assert adapter.supports_session_resume() is True
    assert argv[-2:] == ["--session-id", "123e4567-e89b-12d3-a456-426614174000"]


def test_config_adapter_uses_configured_model_and_prompt_suffix() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    model_flag="--model",
                    model="gpt-5.3-codex-spark",
                    prompt_suffix="General guardrail.",
                    model_prompts=(("gpt-5.3-codex-spark", "Spark guardrail."),),
                ),
            ),
        )
    )

    argv = ConfigAgentAdapter("codex", dict(config.agents)["codex"]).build_argv("base", Path("/plan.md"))

    assert argv[:2] == ["exec", "base\n\nGeneral guardrail.\n\nSpark guardrail."]
    assert argv[argv.index("--model") + 1] == "gpt-5.3-codex-spark"


def test_config_adapter_cli_model_overrides_config_model_for_argv_and_prompt() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    model_flag="--model",
                    model="configured",
                    prompt_suffix="General guardrail.",
                    model_prompts=(
                        ("configured", "Configured-only."),
                        ("cli-model", "CLI-only."),
                    ),
                ),
            ),
        )
    )

    argv = ConfigAgentAdapter("codex", dict(config.agents)["codex"], model="cli-model").build_argv(
        "base",
        Path("/plan.md"),
    )

    assert argv[1] == "base\n\nGeneral guardrail.\n\nCLI-only."
    assert "Configured-only." not in argv[1]
    assert argv[argv.index("--model") + 1] == "cli-model"


def test_config_adapter_reasoning_effort_formats_template_and_cli_overrides_default() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    model_flag="--model",
                    model="configured-model",
                    reasoning_effort="medium",
                    effort_argv=("-c", "model_reasoning_effort={value}"),
                ),
            ),
        )
    )

    argv = ConfigAgentAdapter(
        "codex",
        dict(config.agents)["codex"],
        model="cli-model",
        reasoning_effort="low",
    ).build_argv("base", Path("/plan.md"))

    assert argv == [
        "exec",
        "base",
        "--model",
        "cli-model",
        "-c",
        "model_reasoning_effort=low",
    ]


def test_config_adapter_rejects_reasoning_effort_without_template() -> None:
    config = IssuekitConfig(
        agents=(("claude", AgentRunConfig(binary="claude", headless_argv=("-p",))),)
    )

    with pytest.raises(ValueError, match="reasoning_effort"):
        ConfigAgentAdapter("claude", dict(config.agents)["claude"], reasoning_effort="medium")


def test_config_adapter_model_prompt_requires_exact_match() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    model_flag="--model",
                    model="gpt-5.3-codex-spark-variant",
                    model_prompts=(("gpt-5.3-codex-spark", "Spark guardrail."),),
                ),
            ),
        )
    )

    argv = ConfigAgentAdapter("codex", dict(config.agents)["codex"]).build_argv("base", Path("/plan.md"))

    assert argv[1] == "base"
    assert "Spark guardrail." not in argv[1]


def test_kimi_adapter_parse_output_extracts_resume_id_from_stderr() -> None:
    adapter = resolve_adapter("kimi")
    stdout = "Answer\n"
    stderr = "thinking...\nTo resume this session: kimi -r abc123\n"
    parsed = adapter.parse_output(stdout, stderr)
    assert parsed["resume_session_id"] == "abc123"
    assert parsed["stdout"] == stdout
    assert parsed["stderr"] == stderr


def test_kimi_adapter_resolve_binary_raises_when_not_found(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("issuekit.agentrun.adapter.shutil.which", lambda _cmd: None)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(p).replace("~", str(tmp_path)))
    adapter = resolve_adapter("kimi")
    with pytest.raises(RuntimeError, match="not found"):
        adapter.resolve_binary()



def test_runner_status_gains_last_log_fields_during_run(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "import sys, time; print('log-one', file=sys.stderr); time.sleep(0.8); print('log-two', file=sys.stderr); time.sleep(0.8)"
    )
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
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
    from issuekit.agentrun.runner import _RunWatcher

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
    plan.write_text("plan")
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


def test_runner_prints_agent_runs_note_when_dir_is_created(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
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
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".agent-runs").mkdir()

    adapter = FakeAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0)

    captured = capsys.readouterr()
    assert ".agent-runs/ is gitignored run-log storage" not in captured.err


def test_runner_heartbeat_suppressed_when_stderr_not_tty(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = tmp_path / "script.py"
    script.write_text("import time; time.sleep(0.3)")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
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
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    adapter = FakeAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, agent_prompt(plan), repo, timeout=10.0, follow=True)

    captured = capsys.readouterr()
    assert "running run=" in captured.err
