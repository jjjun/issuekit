from pathlib import Path

import pytest

from issuekit.agentrun.adapters.kimi import KimiAdapter
from issuekit.agents.registry import resolve_adapter
from issuekit.agentrun import ConfigAgentAdapter
from issuekit.config import AgentRunConfig, IssuekitConfig, RoleOverlay, load_config


def test_default_config_includes_kimi_and_codex() -> None:
    config = IssuekitConfig()
    agents_dict = dict(config.agents)
    assert "kimi" in agents_dict
    assert "codex" in agents_dict
    assert agents_dict["kimi"].binary == "kimi"
    assert agents_dict["kimi"].adapter == "kimi"
    assert agents_dict["codex"].binary == "codex"
    assert agents_dict["codex"].adapter is None
    assert agents_dict["codex"].speed is False
    assert agents_dict["codex"].speed_argv == ("-c", "service_tier=priority")


def test_default_config_includes_claude() -> None:
    config = IssuekitConfig()
    agents_dict = dict(config.agents)
    assert "claude" in agents_dict
    assert agents_dict["claude"].binary == "claude"
    assert agents_dict["claude"].adapter is None
    assert agents_dict["claude"].resumable is True
    assert agents_dict["claude"].session_flag == "--session-id"
    assert agents_dict["claude"].effort_argv == ("--effort", "{value}")
    assert agents_dict["claude"].speed is False
    assert agents_dict["claude"].speed_argv == ("--settings", '{"fastMode": true}')


def test_resolve_adapter_returns_kimi() -> None:
    adapter = resolve_adapter("kimi")
    assert isinstance(adapter, KimiAdapter)


def test_resolve_adapter_returns_codex() -> None:
    adapter = resolve_adapter("codex")
    assert isinstance(adapter, ConfigAgentAdapter)
    assert adapter.agent_name == "codex"


def test_resolve_adapter_returns_claude() -> None:
    adapter = resolve_adapter("claude")
    assert isinstance(adapter, ConfigAgentAdapter)
    assert adapter.agent_name == "claude"


def test_claude_adapter_argv_contains_print_and_bypass_permissions() -> None:
    adapter = resolve_adapter("claude")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert argv[0] == "-p"
    assert argv[1].startswith("prompt")
    assert argv[2:6] == [
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "text",
    ]
    assert "acceptEdits" not in argv


def test_claude_adapter_argv_includes_model() -> None:
    adapter = resolve_adapter("claude", model="claude-opus-4-8")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"


def test_claude_adapter_argv_includes_reasoning_effort() -> None:
    adapter = resolve_adapter("claude", reasoning_effort="medium")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert argv[-2:] == ["--effort", "medium"]


def test_claude_adapter_parse_output_returns_streams() -> None:
    adapter = resolve_adapter("claude")
    parsed = adapter.parse_output("stdout text", "stderr text")
    assert parsed["stdout"] == "stdout text"
    assert parsed["stderr"] == "stderr text"


def test_resolve_adapter_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        resolve_adapter("unknown")


def test_resolve_adapter_disabled_agent_raises_clear_error() -> None:
    config = IssuekitConfig(disabled_agents=("kimi",))

    with pytest.raises(ValueError, match="Agent disabled by config: kimi"):
        resolve_adapter("kimi", config=config)


def test_config_agent_adapter_uses_only_run_config() -> None:
    adapter = ConfigAgentAdapter("kimi", AgentRunConfig(binary="kimi"))

    assert adapter.agent_name == "kimi"


def test_resolve_adapter_returns_configured_agent() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "custom",
                AgentRunConfig(binary="custom-agent", headless_argv=("run",)),
            ),
        )
    )

    adapter = resolve_adapter("custom", config=config)

    assert isinstance(adapter, ConfigAgentAdapter)
    assert adapter.agent_name == "custom"


def test_resolve_adapter_uses_configured_custom_adapter() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "custom-kimi",
                AgentRunConfig(
                    binary="custom-kimi",
                    adapter="kimi",
                    headless_argv=("-p",),
                ),
            ),
        )
    )

    adapter = resolve_adapter("custom-kimi", config=config)

    assert isinstance(adapter, KimiAdapter)
    assert adapter.agent_name == "custom-kimi"
    parsed = adapter.parse_output(
        "Answer\n",
        "thinking...\nTo resume this session: custom-kimi -r abc123\n",
    )
    assert parsed["resume_session_id"] == "abc123"


def test_resolve_adapter_rejects_unknown_custom_adapter() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "custom",
                AgentRunConfig(
                    binary="custom",
                    adapter="missing",
                    headless_argv=("run",),
                ),
            ),
        )
    )

    with pytest.raises(ValueError, match="Unknown adapter 'missing'"):
        resolve_adapter("custom", config=config)


def test_resolve_adapter_rejects_builtin_not_present_in_config() -> None:
    config = IssuekitConfig(
        agents=(("codex", AgentRunConfig(binary="codex", headless_argv=("exec",))),)
    )

    with pytest.raises(ValueError, match="Unknown agent"):
        resolve_adapter("kimi", config=config)


def test_resolve_adapter_passes_model() -> None:
    adapter = resolve_adapter("kimi", model="k2")
    assert adapter.model == "k2"


def test_resolve_adapter_passes_reasoning_effort() -> None:
    adapter = resolve_adapter("codex", reasoning_effort="medium")
    assert adapter.reasoning_effort == "medium"


def test_effective_runtime_prefers_run_overrides() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    model="configured-model",
                    reasoning_effort="medium",
                    effort_argv=("-c", "model_reasoning_effort={value}"),
                ),
            ),
        )
    )

    assert resolve_adapter("codex", config=config).effective_runtime() == (
        "configured-model",
        "medium",
    )
    assert resolve_adapter(
        "codex", config=config, model="run-model", reasoning_effort="high"
    ).effective_runtime() == ("run-model", "high")


def test_resolve_adapter_rejects_configured_reasoning_effort_without_template(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.kimi]\nreasoning_effort = 'medium'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ValueError,
        match="add effort_argv to the agent configuration or remove reasoning_effort",
    ):
        resolve_adapter("kimi", config=load_config(tmp_path))


def test_resolve_adapter_rejects_configured_speed_without_template(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.kimi]\nspeed = true\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ValueError,
        match="add speed_argv to the agent configuration or remove speed",
    ):
        resolve_adapter("kimi", config=load_config(tmp_path))


def test_resolve_adapter_rejects_role_reasoning_effort_without_template(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.kimi.roles.reviewer]\nreasoning_effort = 'medium'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ValueError,
        match="add effort_argv to the agent configuration or remove reasoning_effort",
    ):
        resolve_adapter("kimi", config=load_config(tmp_path), role="reviewer")


def test_resolve_adapter_applies_role_overlay_before_agent_default() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "claude",
                AgentRunConfig(
                    binary="claude",
                    headless_argv=("-p",),
                    model_flag="--model",
                    model="claude-sonnet-5",
                    effort_argv=("--effort", "{value}"),
                ),
            ),
        ),
        agent_role_overlays=(
            (
                "claude",
                (
                    (
                        "reviewer",
                        RoleOverlay(model="claude-opus-4-8", reasoning_effort="high"),
                    ),
                ),
            ),
        ),
    )

    implementer = resolve_adapter("claude", config=config, role="implementer")
    reviewer = resolve_adapter("claude", config=config, role="reviewer")
    explicit = resolve_adapter(
        "claude", config=config, role="reviewer", model="claude-haiku-4-5"
    )

    assert implementer.build_argv("prompt", Path("/plan.md"))[-2:] == [
        "--model",
        "claude-sonnet-5",
    ]
    assert reviewer.build_argv("prompt", Path("/plan.md"))[-4:] == [
        "--model",
        "claude-opus-4-8",
        "--effort",
        "high",
    ]
    assert explicit.build_argv("prompt", Path("/plan.md"))[-4:] == [
        "--model",
        "claude-haiku-4-5",
        "--effort",
        "high",
    ]


def test_codex_adapter_argv_contains_exec() -> None:
    adapter = resolve_adapter("codex")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "exec" in argv
    assert argv[1].startswith("prompt")
    assert "--dangerously-bypass-approvals-and-sandbox" in argv


def test_codex_adapter_argv_includes_model() -> None:
    adapter = resolve_adapter("codex", model="gpt-4")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "gpt-4"


def test_codex_adapter_parse_output_returns_streams() -> None:
    adapter = resolve_adapter("codex")
    parsed = adapter.parse_output("stdout text", "stderr text")
    assert parsed["stdout"] == "stdout text"
    assert parsed["stderr"] == "stderr text"


def test_codex_adapter_argv_value_less_approval_flag() -> None:
    config = IssuekitConfig(
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    approval_flag="--full-auto",
                    model_flag="--model",
                ),
            ),
        )
    )
    adapter = ConfigAgentAdapter("codex", dict(config.agents)["codex"])
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert argv == ["exec", "prompt", "--full-auto"]


def test_config_agent_adapter_resolve_binary_uses_path(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-agent"
    fake_bin.write_text("#!/bin/sh\necho ok")
    monkeypatch.setattr(
        "issuekit.agentrun.adapter.shutil.which", lambda _cmd: str(fake_bin)
    )

    class FakeAdapter(ConfigAgentAdapter):
        def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
            return {}

    config = IssuekitConfig(
        agents=(
            (
                "fake",
                AgentRunConfig(binary="fake-agent"),
            ),
        )
    )
    adapter = FakeAdapter("fake", dict(config.agents)["fake"])
    assert adapter.resolve_binary() == fake_bin


def test_load_config_reads_agents(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[agents.custom]\n"
            "binary = 'my-agent'\n"
            "adapter = 'kimi'\n"
            "known_paths = ['/opt/my-agent']\n"
            "headless_argv = ['run']\n"
            "approval_flag = '--approve'\n"
            "approval_value = 'always'\n"
            "model_flag = '--model'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    agents_dict = dict(config.agents)
    assert "custom" in agents_dict
    assert agents_dict["custom"].binary == "my-agent"
    assert agents_dict["custom"].adapter == "kimi"
    assert agents_dict["custom"].known_paths == ("/opt/my-agent",)
    assert agents_dict["custom"].headless_argv == ("run",)
    assert agents_dict["custom"].approval_flag == "--approve"
    assert agents_dict["custom"].approval_value == "always"
    assert agents_dict["custom"].model_flag == "--model"


def test_load_config_preserves_defaults_when_no_agent_table(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 10\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    agents_dict = dict(config.agents)
    assert "kimi" in agents_dict
    assert "codex" in agents_dict
