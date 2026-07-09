import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from issuekit.agents.adapters.kimi import KimiAdapter
from issuekit.agents.runner import AgentAdapter, AgentResult, AgentRunner, ConfigAgentAdapter
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


def test_runner_prompt_describes_api_lifecycle_without_file_tracker(
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
    plan.write_text("plan")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    adapter = PromptCaptureAdapter([sys.executable, str(script)])
    AgentRunner().run(adapter, plan, repo, timeout=10.0)

    assert "Do NOT run git commit or git push" in adapter.prompt
    assert "Write maintainable, idiomatic code" in adapter.prompt
    assert "use normal imports and real identifiers" in adapter.prompt
    assert (
        "Do not split or obfuscate string literals, import paths, or identifiers"
        in adapter.prompt
    )
    assert "unless dynamic loading is truly required" in adapter.prompt
    assert "Issuekit owns the API-backed issue lifecycle" in adapter.prompt
    assert (
        "do not run issuekit claim, submit-review, request-changes, approve, or complete"
        in adapter.prompt
    )
    assert "do not mutate tracker state or issue lifecycle metadata directly" in adapter.prompt
    assert "docs/issues" not in adapter.prompt


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

    adapter = ConfigAgentAdapter("python-agent", config=config)
    result = AgentRunner().run(
        adapter,
        plan,
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


def test_claude_adapter_argv_build_full_shape() -> None:
    adapter = ConfigAgentAdapter("claude")
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
    adapter = ConfigAgentAdapter("claude", model="claude-opus-4-8")
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

    resumable = ConfigAgentAdapter("resumable", config=config)
    plain = ConfigAgentAdapter("plain", config=config)

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
    adapter = ConfigAgentAdapter("claude")
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

    argv = ConfigAgentAdapter("codex", config=config).build_argv("base", Path("/plan.md"))

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

    argv = ConfigAgentAdapter("codex", config=config, model="cli-model").build_argv(
        "base",
        Path("/plan.md"),
    )

    assert argv[1] == "base\n\nGeneral guardrail.\n\nCLI-only."
    assert "Configured-only." not in argv[1]
    assert argv[argv.index("--model") + 1] == "cli-model"


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

    argv = ConfigAgentAdapter("codex", config=config).build_argv("base", Path("/plan.md"))

    assert argv[1] == "base"
    assert "Spark guardrail." not in argv[1]


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
    result = AgentRunner().run(adapter, plan, repo, timeout=10.0)

    assert result.status_path is not None
    final_status = json.loads(result.status_path.read_text(encoding="utf-8"))
    assert final_status["last_log_line"] == "log-two"
    assert final_status["last_log_at"] is not None
    assert final_status["heartbeat_at"] is not None


def test_runner_writer_survives_a_failing_tick(tmp_path: Path, monkeypatch) -> None:
    from issuekit.agents.runner import _RunWatcher

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
    result = AgentRunner().run(adapter, plan, repo, timeout=10.0)

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
    AgentRunner().run(adapter, plan, repo, timeout=10.0)

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
    AgentRunner().run(adapter, plan, repo, timeout=10.0)

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
    AgentRunner().run(adapter, plan, repo, timeout=10.0)

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
    AgentRunner().run(adapter, plan, repo, timeout=10.0, follow=True)

    captured = capsys.readouterr()
    assert "running run=" in captured.err
