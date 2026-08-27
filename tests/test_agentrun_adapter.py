import json
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


def _json_adapter() -> ConfigAgentAdapter:
    return ConfigAgentAdapter(
        "claude",
        AgentRunConfig(
            binary="claude",
            headless_argv=("-p",),
            output_format_flag="--output-format",
            output_format="json",
        ),
    )


def test_config_adapter_unwraps_json_result_envelope() -> None:
    envelope = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"verdict": "agree"}',
            "session_id": "518533be-abe7-4728-aa20-549bbeefe661",
            "total_cost_usd": 0.0695405,
            "usage": {
                "input_tokens": 3503,
                "cache_creation_input_tokens": 4019,
                "cache_read_input_tokens": 22261,
                "output_tokens": 4,
                "service_tier": "standard",
            },
        }
    )

    parsed = _json_adapter().parse_output(envelope, "stderr text")

    assert parsed["stdout"] == '{"verdict": "agree"}'
    assert parsed["stderr"] == "stderr text"
    assert parsed["session_id"] == "518533be-abe7-4728-aa20-549bbeefe661"
    assert parsed["cost_usd"] == "0.0695405"
    assert parsed["usage_input_tokens"] == "3503"
    assert parsed["usage_cache_read_input_tokens"] == "22261"
    assert parsed["usage_output_tokens"] == "4"
    # Non-numeric usage entries are metadata, not counts.
    assert "usage_service_tier" not in parsed


def test_config_adapter_unwraps_json_result_envelope_success_has_no_failure_reason() -> None:
    envelope = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"verdict": "agree"}',
        }
    )

    parsed = _json_adapter().parse_output(envelope, "")

    assert parsed["is_error"] == "false"
    assert "failure_reason" not in parsed


def test_config_adapter_parses_startup_failure_envelope() -> None:
    envelope = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "terminal_reason": "api_error",
            "num_turns": 1,
            "result": "Failed to authenticate: OAuth session expired and could "
            "not be refreshed",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )

    parsed = _json_adapter().parse_output(envelope, "workspace-trust warning")

    assert parsed["is_error"] == "true"
    assert parsed["terminal_reason"] == "api_error"
    assert parsed["num_turns"] == "1"
    assert parsed["failure_reason"] == (
        "Failed to authenticate: OAuth session expired and could not be refreshed"
    )
    assert parsed["usage_input_tokens"] == "0"
    assert parsed["usage_output_tokens"] == "0"


def test_config_adapter_keeps_raw_stdout_when_envelope_is_unparsable() -> None:
    parsed = _json_adapter().parse_output("crashed before any JSON", "boom")

    assert parsed == {"stdout": "crashed before any JSON", "stderr": "boom"}


def test_config_adapter_does_not_unwrap_text_output_format() -> None:
    adapter = ConfigAgentAdapter(
        "kimi",
        AgentRunConfig(
            binary="kimi",
            headless_argv=("-p",),
            output_format_flag="--output-format",
            output_format="text",
        ),
    )
    envelope = json.dumps({"result": "inner", "usage": {"input_tokens": 1}})

    assert adapter.parse_output(envelope, "")["stdout"] == envelope


def test_config_adapter_resume_uses_resume_flag() -> None:
    session_id = "123e4567-e89b-12d3-a456-426614174000"
    adapter = ConfigAgentAdapter(
        "continuable",
        AgentRunConfig(
            binary="agent",
            headless_argv=("run",),
            resumable=True,
            session_flag="--session-id",
            resume_flag="--resume",
        ),
    )

    assert adapter.supports_session_continuation() is True
    assert adapter.build_argv("prompt", Path("/plan.md"), session_id=session_id)[-2:] == [
        "--session-id",
        session_id,
    ]
    assert adapter.build_argv(
        "prompt", Path("/plan.md"), session_id=session_id, resume=True
    )[-2:] == ["--resume", session_id]


def test_config_adapter_rejects_resume_without_resume_flag() -> None:
    adapter = ConfigAgentAdapter(
        "session-only",
        AgentRunConfig(
            binary="agent",
            headless_argv=("run",),
            resumable=True,
            session_flag="--session-id",
        ),
    )

    assert adapter.supports_session_continuation() is False
    with pytest.raises(ValueError, match="cannot continue a session"):
        adapter.build_argv(
            "prompt",
            Path("/plan.md"),
            session_id="123e4567-e89b-12d3-a456-426614174000",
            resume=True,
        )


def test_config_adapter_compose_prompt_matches_argv_prompt() -> None:
    adapter = ConfigAgentAdapter(
        "codex",
        AgentRunConfig(
            binary="codex",
            headless_argv=("exec",),
            model_flag="--model",
            model="gpt-5.3-codex",
            prompt_suffix="Make minimal, additive diffs.",
            model_prompts=(("gpt-5.3-codex", "Codex guardrail."),),
        ),
    )

    composed = adapter.compose_prompt("base")

    assert composed == (
        "base\n\nMake minimal, additive diffs.\n\nCodex guardrail."
    )
    assert adapter.build_argv("base", Path("/plan.md"))[1] == composed


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
