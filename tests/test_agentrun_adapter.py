from pathlib import Path

import pytest

from issuekit.agentrun import AgentRunConfig, ConfigAgentAdapter
from issuekit.agents.registry import resolve_adapter


def test_config_adapter_appends_session_flag_only_when_resumable() -> None:
    resumable = ConfigAgentAdapter(
        "resumable",
        AgentRunConfig(
            binary="agent",
            headless_argv=("run",),
            resumable=True,
            session_flag="--session-id",
        ),
    )
    plain = ConfigAgentAdapter(
        "plain",
        AgentRunConfig(
            binary="agent",
            headless_argv=("run",),
            session_flag="--session-id",
        ),
    )

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


def test_config_adapter_appends_session_id_when_supplied() -> None:
    adapter = resolve_adapter("claude")
    argv = adapter.build_argv(
        "prompt",
        Path("/plan.md"),
        session_id="123e4567-e89b-12d3-a456-426614174000",
    )

    assert adapter.supports_session_resume() is True
    assert argv[-2:] == ["--session-id", "123e4567-e89b-12d3-a456-426614174000"]


def test_config_adapter_uses_configured_model_and_prompt_suffix() -> None:
    adapter = ConfigAgentAdapter(
        "codex",
        AgentRunConfig(
            binary="codex",
            headless_argv=("exec",),
            model_flag="--model",
            model="gpt-5.3-codex-spark",
            prompt_suffix="General guardrail.",
            model_prompts=(("gpt-5.3-codex-spark", "Spark guardrail."),),
        ),
    )

    argv = adapter.build_argv("base", Path("/plan.md"))

    assert argv[:2] == ["exec", "base\n\nGeneral guardrail.\n\nSpark guardrail."]
    assert argv[argv.index("--model") + 1] == "gpt-5.3-codex-spark"


def test_config_adapter_run_model_overrides_config_model() -> None:
    adapter = ConfigAgentAdapter(
        "codex",
        AgentRunConfig(
            binary="codex",
            headless_argv=("exec",),
            model_flag="--model",
            model="configured",
            prompt_suffix="General guardrail.",
            model_prompts=(
                ("configured", "Configured-only."),
                ("run-model", "Run-only."),
            ),
        ),
        model="run-model",
    )

    argv = adapter.build_argv("base", Path("/plan.md"))

    assert argv[1] == "base\n\nGeneral guardrail.\n\nRun-only."
    assert "Configured-only." not in argv[1]
    assert argv[argv.index("--model") + 1] == "run-model"
    assert adapter.effective_runtime() == ("run-model", None)


def test_config_adapter_reasoning_effort_formats_template() -> None:
    adapter = ConfigAgentAdapter(
        "codex",
        AgentRunConfig(
            binary="codex",
            headless_argv=("exec",),
            model_flag="--model",
            model="configured-model",
            reasoning_effort="medium",
            effort_argv=("-c", "model_reasoning_effort={value}"),
        ),
        model="run-model",
        reasoning_effort="low",
    )

    argv = adapter.build_argv("base", Path("/plan.md"))

    assert argv == [
        "exec",
        "base",
        "--model",
        "run-model",
        "-c",
        "model_reasoning_effort=low",
    ]
    assert adapter.effective_runtime() == ("run-model", "low")


def test_config_adapter_speed_emits_after_effort_before_session() -> None:
    adapter = ConfigAgentAdapter(
        "codex",
        AgentRunConfig(
            binary="codex",
            headless_argv=("exec",),
            resumable=True,
            session_flag="--session-id",
            approval_flag="--dangerously-bypass-approvals-and-sandbox",
            model_flag="--model",
            model="gpt-5.6-sol",
            reasoning_effort="ultra",
            effort_argv=("-c", "model_reasoning_effort={value}"),
            speed=True,
            speed_argv=("-c", "service_tier=priority"),
        ),
    )

    argv = adapter.build_argv(
        "base",
        Path("/plan.md"),
        session_id="123e4567-e89b-12d3-a456-426614174000",
    )

    assert argv == [
        "exec",
        "base",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        "gpt-5.6-sol",
        "-c",
        "model_reasoning_effort=ultra",
        "-c",
        "service_tier=priority",
        "--session-id",
        "123e4567-e89b-12d3-a456-426614174000",
    ]


def test_config_adapter_false_speed_emits_nothing() -> None:
    run_config = AgentRunConfig(
        binary="codex",
        headless_argv=("exec",),
        speed=False,
        speed_argv=("-c", "service_tier=priority"),
    )

    argv = ConfigAgentAdapter("codex", run_config).build_argv(
        "base",
        Path("/plan.md"),
    )

    assert argv == ["exec", "base"]


def test_config_adapter_rejects_reasoning_effort_without_template() -> None:
    with pytest.raises(
        ValueError,
        match="add effort_argv to the agent configuration or remove reasoning_effort",
    ):
        ConfigAgentAdapter(
            "claude",
            AgentRunConfig(binary="claude", headless_argv=("-p",)),
            reasoning_effort="medium",
        )


def test_config_adapter_model_prompt_requires_exact_match() -> None:
    adapter = ConfigAgentAdapter(
        "codex",
        AgentRunConfig(
            binary="codex",
            headless_argv=("exec",),
            model_flag="--model",
            model="gpt-5.3-codex-spark-variant",
            model_prompts=(("gpt-5.3-codex-spark", "Spark guardrail."),),
        ),
    )

    argv = adapter.build_argv("base", Path("/plan.md"))

    assert argv[1] == "base"
    assert "Spark guardrail." not in argv[1]


def test_kimi_adapter_argv_contains_headless_flags_and_model() -> None:
    adapter = resolve_adapter("kimi", model="k2")
    argv = adapter.build_argv("prompt", Path("/plan.md"))

    assert "-p" in argv
    assert "--auto" not in argv
    assert "-y" not in argv
    assert "--output-format" in argv
    assert argv[argv.index("-m") + 1] == "k2"


def test_kimi_adapter_parse_output_extracts_resume_id_from_stderr() -> None:
    adapter = resolve_adapter("kimi")
    stdout = "Answer\n"
    stderr = "thinking...\nTo resume this session: kimi -r abc123\n"

    parsed = adapter.parse_output(stdout, stderr)

    assert parsed["resume_session_id"] == "abc123"
    assert parsed["stdout"] == stdout
    assert parsed["stderr"] == stderr


@pytest.mark.parametrize(
    "marker",
    (
        "To resume this session:",
        "To resume this session: kimi -r",
        "To resume this session: kimi -r abc123 extra",
    ),
)
def test_kimi_adapter_parse_output_ignores_malformed_resume_marker(
    marker: str,
) -> None:
    adapter = resolve_adapter("kimi")

    parsed = adapter.parse_output("", f"{marker}\n")

    assert "resume_session_id" not in parsed


def test_kimi_adapter_resolve_binary_raises_when_not_found(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("issuekit.agentrun.adapter.shutil.which", lambda _cmd: None)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(p).replace("~", str(tmp_path)))
    adapter = resolve_adapter("kimi")

    with pytest.raises(RuntimeError, match="not found"):
        adapter.resolve_binary()
