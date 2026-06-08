from pathlib import Path

import pytest

from issuekit.agents.adapters.codex import CodexAdapter
from issuekit.agents.adapters.kimi import KimiAdapter
from issuekit.agents.runner import ConfigAgentAdapter, resolve_adapter
from issuekit.config import AgentRunConfig, IssuekitConfig, load_config


def test_default_config_includes_kimi_and_codex() -> None:
    config = IssuekitConfig()
    agents_dict = dict(config.agents)
    assert "kimi" in agents_dict
    assert "codex" in agents_dict
    assert agents_dict["kimi"].binary == "kimi"
    assert agents_dict["codex"].binary == "codex"


def test_resolve_adapter_returns_kimi() -> None:
    adapter = resolve_adapter("kimi")
    assert isinstance(adapter, KimiAdapter)


def test_resolve_adapter_returns_codex() -> None:
    adapter = resolve_adapter("codex")
    assert isinstance(adapter, CodexAdapter)


def test_resolve_adapter_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        resolve_adapter("unknown")


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


def test_resolve_adapter_passes_model() -> None:
    adapter = resolve_adapter("kimi", model="k2")
    assert adapter.model == "k2"


def test_codex_adapter_argv_contains_exec() -> None:
    adapter = CodexAdapter()
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "exec" in argv
    assert "prompt" in argv
    assert "--full-auto" in argv


def test_codex_adapter_argv_includes_model() -> None:
    adapter = CodexAdapter(model="gpt-4")
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "gpt-4"


def test_codex_adapter_parse_output_returns_streams() -> None:
    adapter = CodexAdapter()
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
    adapter = CodexAdapter(config=config)
    argv = adapter.build_argv("prompt", Path("/plan.md"))
    assert argv == ["exec", "prompt", "--full-auto"]


def test_config_agent_adapter_resolve_binary_uses_path(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-agent"
    fake_bin.write_text("#!/bin/sh\necho ok")
    monkeypatch.setattr(
        "issuekit.agents.runner.shutil.which", lambda _cmd: str(fake_bin)
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
    adapter = FakeAdapter("fake", config=config)
    assert adapter.resolve_binary() == fake_bin


def test_load_config_reads_agents(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[agents.custom]\n"
            "binary = 'my-agent'\n"
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
